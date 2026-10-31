"""Sign-in for the demo UI: users, sessions and the middleware that guards every page and API.

Users and sessions live in a small SQLite file on the `ui-data` volume, so they survive restarts
and `./scripts/reset.sh` clears them with everything else. The first start seeds one user,
`admin` with password `admin`, who has to choose a new password before seeing anything.

  POST /api/auth/login      {username, password}: starts a session (cookie `vtz_session`)
  POST /api/auth/password   {current, new}: changes the signed-in user's password
  POST /api/auth/logout     ends the session
  GET  /api/auth/me         the signed-in user, or 401
  GET  /api/auth/state      whether the seeded admin still has the default password (public)
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

DB_FILE = Path(os.environ.get("AUTH_DB", "/data/auth.db"))
COOKIE = "vtz_session"
SESSION_SECONDS = int(os.environ.get("SESSION_HOURS", 12)) * 3600
MIN_PASSWORD = 8

# Paths anyone may load: the sign-in page, its assets and the sign-in API itself.
PUBLIC = ("/login", "/static/login.", "/static/theme.js", "/api/auth/login", "/api/auth/state")

router = APIRouter(prefix="/api/auth")
_lock = threading.Lock()


# ------------------------------------------------------------------------------- storage

def db():
    conn = sqlite3.connect(DB_FILE, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    """Creates the tables and, on the first start, the `admin` user (password `admin`)."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as conn:
        conn.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                must_change   INTEGER NOT NULL DEFAULT 1,
                created_at    INTEGER NOT NULL,
                changed_at    INTEGER
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
        """)
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            conn.execute("INSERT INTO users (username, password_hash, must_change, created_at) "
                         "VALUES ('admin', ?, 1, ?)", (hash_password("admin"), int(time.time())))
            print("Seeded UI user admin (password admin, to be changed at first sign-in)", flush=True)


# scrypt from the standard library: memory-hard, so no extra dependency is needed.
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT, dklen=32)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def check_password(password, stored):
    try:
        _, n, r, p, salt, digest = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                n=int(n), r=int(r), p=int(p), dklen=32)
    except ValueError:
        return False
    return hmac.compare_digest(actual.hex(), digest)


# A real hash to check against when the username does not exist, so both cases take as long.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session_user(token):
    """The user row for a session token, or None if it is unknown or expired."""
    if not token:
        return None
    with closing(db()) as conn:
        return conn.execute(
            "SELECT u.username, u.must_change FROM sessions s JOIN users u USING (username) "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (_token_hash(token), int(time.time()))).fetchone()


# ------------------------------------------------------------------------------- throttling

# Failed sign-ins per client address: after MAX_FAILURES in WINDOW seconds, wait for the window.
# Sign-ins run on several threads, so the table has its own lock; it forgets addresses whose
# failures are older than the window, and holds at most MAX_CLIENTS of them.
MAX_FAILURES, WINDOW, MAX_CLIENTS = 5, 60, 10_000
_failures = {}
_failures_lock = threading.Lock()


def _recent(client, now):
    return [t for t in _failures.get(client, ()) if now - t < WINDOW]


def _throttled(client):
    now = time.monotonic()
    with _failures_lock:
        recent = _recent(client, now)
        if recent:
            _failures[client] = recent
        else:
            _failures.pop(client, None)
        return len(recent) >= MAX_FAILURES


def _failed(client):
    now = time.monotonic()
    with _failures_lock:
        if len(_failures) >= MAX_CLIENTS:
            for stale in [c for c in _failures if not _recent(c, now)]:
                del _failures[stale]
            if len(_failures) >= MAX_CLIENTS:
                # Still full of active addresses: drop the one whose last failure is oldest.
                del _failures[min(_failures, key=lambda c: _failures[c][-1])]
        _failures.setdefault(client, []).append(now)
        _failures[client] = _failures[client][-MAX_FAILURES:]


def _succeeded(client):
    with _failures_lock:
        _failures.pop(client, None)


# ------------------------------------------------------------------------------- API

class Login(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current: str
    new: str


def _client(request):
    return request.client.host if request.client else "?"


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    client = _client(request)
    if _throttled(client):
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    with closing(db()) as conn:
        user = conn.execute("SELECT username, password_hash, must_change FROM users "
                            "WHERE username = ?", (body.username.strip().lower(),)).fetchone()
        if not check_password(body.password, user["password_hash"] if user else _DUMMY_HASH) or not user:
            _failed(client)
            raise HTTPException(401, "That username and password don't match.")
        _succeeded(client)
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with _lock:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                         (_token_hash(token), user["username"], now + SESSION_SECONDS))
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")
    return {"username": user["username"], "must_change": bool(user["must_change"])}


def password_problem(new, current, username):
    """Why `new` is not acceptable, or None."""
    if len(new) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if new == current:
        return "Choose a password different from the current one."
    if new.lower() in {"admin", "password", username.lower()} or new.lower().startswith("password"):
        return "That password is too easy to guess."
    if len(set(new)) < 4:
        return "Use a wider mix of characters."
    return None


@router.post("/password")
def change_password(body: PasswordChange, request: Request):
    token = request.cookies.get(COOKIE)
    user = session_user(token)
    if not user:
        raise HTTPException(401, "Sign in first.")
    with closing(db()) as conn:
        stored = conn.execute("SELECT password_hash FROM users WHERE username = ?",
                              (user["username"],)).fetchone()["password_hash"]
        if not check_password(body.current, stored):
            raise HTTPException(400, "Your current password isn't right.")
        problem = password_problem(body.new, body.current, user["username"])
        if problem:
            raise HTTPException(400, problem)
        with _lock:
            conn.execute("UPDATE users SET password_hash = ?, must_change = 0, changed_at = ? "
                         "WHERE username = ?", (hash_password(body.new), int(time.time()), user["username"]))
            # Other sessions of this user end; this one carries on.
            conn.execute("DELETE FROM sessions WHERE username = ? AND token_hash != ?",
                         (user["username"], _token_hash(token)))
    return {"username": user["username"], "must_change": False}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with closing(db()) as conn, _lock:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user = session_user(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Not signed in.")
    return {"username": user["username"], "must_change": bool(user["must_change"])}


@router.get("/state")
def state():
    """Lets the sign-in page show the default credentials only while they still work."""
    with closing(db()) as conn:
        row = conn.execute("SELECT must_change FROM users WHERE username = 'admin'").fetchone()
    return {"first_run": bool(row and row["must_change"])}


# ------------------------------------------------------------------------------- middleware

class RequireLogin:
    """Pure ASGI middleware (BaseHTTPMiddleware would buffer the event stream).

    Pages redirect to /login; API calls get 401. A user who still has to change their password
    may only reach /login and the auth API (403 elsewhere).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path.startswith(PUBLIC):
            return await self.app(scope, receive, send)
        cookie = SimpleCookie()
        for name, value in scope["headers"]:
            if name == b"cookie":
                try:
                    cookie.load(value.decode("latin-1"))
                except CookieError:
                    pass
        token = cookie[COOKIE].value if COOKIE in cookie else None
        user = session_user(token)
        api = path.startswith("/api/")
        if user and (not user["must_change"] or path.startswith("/api/auth/")):
            scope.setdefault("state", {})["user"] = user["username"]
            return await self.app(scope, receive, send)
        if api:
            status, detail = (403, "Change your password first.") if user else (401, "Not signed in.")
            response = JSONResponse({"detail": detail}, status_code=status)
        else:
            response = RedirectResponse("/login", status_code=303)
        await response(scope, receive, send)

"""The demo UI: live events as Vectezium captures them, the lake (Azurite) and the source tables,
for either connector: `shop` (MySQL) or `shop_pg` (PostgreSQL).

  /api/connectors      the connectors the UI can show
  /api/events          server-sent events: each line of the connector's live file as it is written
  /api/events/find     one event from the live file by seq (the browser keeps large events truncated)
  /api/status          the published offset and the single-writer lock
  /api/lake            one folder of the connector's folder in the lake
  /api/lake/blob       one blob's content (gunzipped), parsed as NDJSON
  /api/db/tables       the source database's captured schema: its tables and their row counts
  /api/db/rows         the first rows of one table
  /api/snapshots       each table's snapshot state and the signals sent to Vectezium (GET);
                       sends a signal: snapshot, stop, dismiss a held snapshot, approve (POST)
  /api/licences        Vectezium's licence, notice and the crates its CDC sources add
  /api/auth/*          sign-in, password change and sign-out (auth.py)

The connector endpoints take `connector`: `mysql` (the default) or `postgres`. Everything except
the sign-in page needs a session: see auth.py.
"""

import asyncio
import contextlib
import csv
import ctypes
import datetime
import decimal
import json
import os
import re
import time
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

import pg8000.native
import pymysql
import yaml
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobPrefix, BlobServiceClient
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import auth

EVENTS_DIR = Path(os.environ.get("EVENTS_DIR", "/events"))
CONTAINER = os.environ.get("LAKE_CONTAINER", "raw")
BACKLOG_BYTES = 2_000_000
MAX_PREVIEW_LINES = 5000
LICENCES = Path(os.environ.get("LICENCES_DIR", "/licences"))
# Vectezium's signal folders (one per source, see vectezium.yaml), and its config, for the tables
# each source captures.
SIGNALS = Path(os.environ.get("SIGNALS_DIR", "/signals"))
VECTEZIUM_CONFIG = Path(os.environ.get("VECTEZIUM_CONFIG", "/config/vectezium.yaml"))

# live.ndjson is cut back to its newest LIVE_KEEP_BYTES once it grows past LIVE_MAX_BYTES, so it
# stays small however much is captured. A browser is sent at most STREAM_CHUNK_BYTES at a time,
# and one that falls more than MAX_LAG_BYTES behind skips ahead to the newest events.
LIVE_MAX_BYTES = int(os.environ.get("LIVE_MAX_BYTES", 64_000_000))
LIVE_KEEP_BYTES = int(os.environ.get("LIVE_KEEP_BYTES", 8_000_000))
STREAM_CHUNK_BYTES = 1_000_000
MAX_LAG_BYTES = 16_000_000


@dataclass(frozen=True)
class Connector:
    """One of the demo's connectors: a Vectezium source and what it reads and writes."""
    id: str            # the Vectezium source, and the value of the `connector` parameter
    label: str
    name: str          # topic_prefix: the connector's name, and its folder in the lake
    events: str        # the file in EVENTS_DIR its live sink writes
    database: str
    schema: str        # the schema its tables are named in: <schema>.<table>

    @property
    def root(self):
        return f"cdc/{self.name}/"


CONNECTORS = {c.id: c for c in [
    Connector("mysql", "MySQL", "shop", "live.ndjson", "shop", "shop"),
    Connector("postgres", "PostgreSQL", "shop_pg", "live_pg.ndjson", "shop", "public"),
]}


def get_connector(connector):
    try:
        return CONNECTORS[connector]
    except KeyError:
        raise HTTPException(404, f"no connector {connector!r}: {', '.join(CONNECTORS)}")


@contextlib.asynccontextmanager
async def lifespan(_app):
    auth.init()
    task = asyncio.create_task(trim_live_file())
    yield
    task.cancel()


STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Vectezium demo", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(auth.router)
app.add_middleware(auth.RequireLogin)
lake = BlobServiceClient.from_connection_string(
    os.environ["AZURE_STORAGE_CONNECTION_STRING"]).get_container_client(CONTAINER)


# Everything the UI loads comes from this server; no inline scripts or styles, no framing.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                               "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                               "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


@app.middleware("http")
async def response_headers(request: Request, call_next):
    """Security headers on every response. Browsers check /static/ files on every load, so a new
    UI never runs with a cached old app.js."""
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})


@app.get("/api/connectors")
def connectors():
    return [{"id": c.id, "label": c.label, "name": c.name, "root": c.root,
             "database": c.database, "schema": c.schema} for c in CONNECTORS.values()]


# ------------------------------------------------------------------------------- live events

class LiveFile:
    """A connector's live file (live.ndjson, live_pg.ndjson), trimmed in place so it stays small.

    Positions are logical: bytes ever written to the file, so they stay valid when its start is
    cut off. `start` is the first logical byte still in the file; `generation` changes when the
    file is replaced (deleted, recreated, truncated by someone else), which resets positions.
    """

    def __init__(self, path):
        self.path = path
        self.generation = 0
        self.inode = None
        self.start = 0          # bytes cut off the front so far
        self.size = None        # physical size at the last refresh; None if there is no file

    @property
    def end(self):
        return self.start + self.size

    def refresh(self):
        try:
            st = self.path.stat()
        except FileNotFoundError:
            self.size = None
            return
        if st.st_ino != self.inode or (self.size is not None and st.st_size < self.size):
            self.generation += 1
            self.inode, self.start = st.st_ino, 0
        self.size = st.st_size

    def read(self, pos, length):
        with open(self.path, "rb") as f:
            f.seek(pos - self.start)
            return f.read(length)

    def trim(self):
        """Cuts the file back to about its newest LIVE_KEEP_BYTES once it is over LIVE_MAX_BYTES."""
        self.refresh()
        if self.size is None or self.size <= LIVE_MAX_BYTES:
            return
        # Collapsing a range keeps Vectezium's appends intact (it writes with O_APPEND); it needs
        # whole filesystem blocks, so the new first line is usually cut off (readers skip it).
        cut = (self.size - LIVE_KEEP_BYTES) // 4096 * 4096
        with open(self.path, "r+b") as f:
            if _fallocate(f.fileno(), FALLOC_FL_COLLAPSE_RANGE, 0, cut) != 0:
                # Not supported here: empty the file instead (open viewers lose the backlog).
                os.ftruncate(f.fileno(), 0)
                cut = self.size
        self.start += cut
        self.size -= cut


FALLOC_FL_COLLAPSE_RANGE = 0x08
_fallocate = ctypes.CDLL(None, use_errno=True).fallocate
_fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_long, ctypes.c_long]

live_files = {c.id: LiveFile(EVENTS_DIR / c.events) for c in CONNECTORS.values()}


async def trim_live_file():
    while True:
        for live in live_files.values():
            try:
                live.trim()
            except OSError as error:
                print(f"Could not trim {live.path}: {error}", flush=True)
        # Often: a burst of large rows can add ~100 MB a second (one stat call when nothing to do).
        await asyncio.sleep(1)


def sse(line):
    return f"data: {line.decode('utf-8', 'replace')}\n\n"


@app.get("/api/events")
async def events(request: Request, backlog: int = 500, connector: str = "mysql"):
    """Streams the connector's live file: the last `backlog` lines, then every new line as it is written.

    Sends at most STREAM_CHUNK_BYTES per step, waiting for the browser between steps. A browser
    that falls more than MAX_LAG_BYTES behind (or behind the trimmed start of the file) skips
    ahead and gets a `gap` event with the number of bytes it missed.
    """
    live = live_files[get_connector(connector).id]

    async def stream():
        generation, pos, partial, realign = None, 0, b"", False
        idle = 0.0
        while not await request.is_disconnected():
            live.refresh()
            if live.size is None:
                if generation is None:
                    generation = -1
                    yield "event: caught-up\ndata: {}\n\n"
            elif generation != live.generation:
                # First read, or the file was replaced: start from its last `backlog` lines.
                generation = live.generation
                end = live.end
                pos = max(live.start, end - BACKLOG_BYTES)
                data = live.read(pos, end - pos)
                if pos > 0:
                    data = data.split(b"\n", 1)[-1]   # drop the cut-off first line
                pos, realign = end, False
                complete, partial = split_lines(data)
                for line in complete[-backlog:]:
                    yield sse(line)
                yield "event: caught-up\ndata: {}\n\n"
            elif live.end > pos:
                end = live.end
                if pos < live.start or end - pos > MAX_LAG_BYTES:
                    resume = max(live.start, end - STREAM_CHUNK_BYTES)
                    skipped = resume - pos
                    pos, partial, realign = resume, b"", True
                    yield f"event: gap\ndata: {json.dumps({'bytes': skipped})}\n\n"
                chunk = live.read(pos, min(end - pos, STREAM_CHUNK_BYTES))
                pos += len(chunk)
                if realign:
                    chunk, realign = chunk.split(b"\n", 1)[-1], False
                complete, partial = split_lines(partial + chunk)
                for line in complete:
                    yield sse(line)
                idle = 0.0
                if pos < end:
                    continue        # more to send: no pause
            await asyncio.sleep(0.25)
            idle += 0.25
            if idle >= 15:
                yield ": keep-alive\n\n"
                idle = 0.0

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/events/find")
def find_event(seq: str, connector: str = "mysql"):
    """The full event with this `seq` (not the published offset that shares it), if it is still
    in the connector's live file."""
    needle = f'"seq":"{seq}"'.encode()
    try:
        # Read, not mmap: the trimmer shrinks the file in place, and touching a mapped page past
        # the new end would kill the process (SIGBUS). The file is at most LIVE_MAX_BYTES.
        with open(live_files[get_connector(connector).id].path, "rb") as f:
            data = f.read()
        at = data.rfind(needle)
        while at >= 0:
            line_start = data.rfind(b"\n", 0, at) + 1
            line_end = data.find(b"\n", at)
            event = json.loads(data[line_start:line_end if line_end >= 0 else len(data)])
            if "change_seq" not in event:
                return event
            at = data.rfind(needle, 0, line_start)
    except (FileNotFoundError, ValueError):
        pass
    raise HTTPException(404, "no longer in the live file")


def split_lines(data):
    *complete, partial = data.split(b"\n")
    return [line for line in complete if line.strip()], partial


# ------------------------------------------------------------------------------- the lake

def published_offset(c):
    """The connector's last published offset (`_offsets/latest.json`), or None."""
    try:
        text = lake.get_blob_client(c.root + "_offsets/latest.json").download_blob().readall()
        return json.loads(text.splitlines()[0])
    except (ResourceNotFoundError, IndexError, ValueError):
        return None


@app.get("/api/status")
def status(connector: str = "mysql"):
    c = get_connector(connector)
    root = c.root
    result = {"offset": published_offset(c), "lock": None}
    try:
        lease = lake.get_blob_client(root + "_lock").get_blob_properties().lease
        result["lock"] = {"state": lease.state, "status": lease.status}
    except ResourceNotFoundError:
        pass
    return result


@app.get("/api/lake")
def lake_list(prefix: str = "", connector: str = "mysql"):
    """One folder of the connector's folder (`prefix`, relative to it): its subfolders and its
    files, newest first. Listing one level keeps each call small however large the lake gets."""
    if prefix and (not prefix.endswith("/") or ".." in prefix.split("/")):
        raise HTTPException(400, "prefix must be a folder such as shop/customers/")
    root = get_connector(connector).root
    folders, files = [], []
    for item in lake.walk_blobs(name_starts_with=root + prefix, delimiter="/"):
        if isinstance(item, BlobPrefix):
            folders.append(item.name[len(root + prefix):].rstrip("/"))
        else:
            files.append({"name": item.name, "size": item.size,
                          "last_modified": item.last_modified.isoformat()})
    files.sort(key=lambda f: f["last_modified"], reverse=True)
    return {"container": CONTAINER, "root": root, "prefix": prefix,
            "folders": sorted(folders), "files": files}


@app.get("/api/lake/blob")
def lake_blob(name: str, connector: str = "mysql"):
    if not name.startswith(get_connector(connector).root):
        raise HTTPException(400, "outside the connector's folder")
    try:
        download = lake.get_blob_client(name).download_blob()
    except ResourceNotFoundError:
        raise HTTPException(404, "no such blob")
    stored = download.properties.size
    # Streamed: decompressed and split as it downloads, stopping after MAX_PREVIEW_LINES, so a
    # large file is never held in memory whole. (Vector sets Content-Encoding: gzip on
    # compressed blobs; the client may already have unzipped them, hence the magic check.)
    lines, size, complete = [], 0, True
    inflate, pending = None, b""
    for chunk in download.chunks():
        if inflate is None:
            inflate = zlib.decompressobj(wbits=47) if chunk[:2] == b"\x1f\x8b" else False
        data = chunk
        if inflate:
            data = inflate.decompress(chunk)
            while inflate.eof and inflate.unused_data:   # another gzip member
                rest = inflate.unused_data
                inflate = zlib.decompressobj(wbits=47)
                data += inflate.decompress(rest)
        size += len(data)
        *new, pending = (pending + data).split(b"\n")
        lines += [line for line in new if line.strip()]
        if len(lines) > MAX_PREVIEW_LINES:
            complete = False
            break
    if complete and pending.strip():
        lines.append(pending)
    records = []
    for line in lines[:MAX_PREVIEW_LINES]:
        text = line.decode("utf-8", "replace")
        try:
            records.append(json.loads(text))
        except ValueError:
            records.append(text)
    return {"name": name, "stored_bytes": stored, "bytes": size if complete else None,
            "lines": len(lines) if complete else None, "records": records,
            "truncated": not complete}


# ------------------------------------------------------------------------------- the databases

def mysql():
    return pymysql.connect(host=os.environ.get("MYSQL_HOST", "mysql"),
                           user=os.environ.get("MYSQL_USER", "app"),
                           password=os.environ.get("MYSQL_PASSWORD", ""),
                           database=CONNECTORS["mysql"].database,
                           cursorclass=pymysql.cursors.DictCursor, connect_timeout=5)


def postgres():
    return pg8000.native.Connection(host=os.environ.get("POSTGRES_HOST", "postgres"),
                                    user=os.environ.get("POSTGRES_USER", "app"),
                                    password=os.environ.get("POSTGRES_PASSWORD", ""),
                                    database=CONNECTORS["postgres"].database, timeout=5)


def jsonable(value):
    if value is None or isinstance(value, (str, int, float, dict)):
        return value
    if isinstance(value, list):               # PostgreSQL arrays
        return [jsonable(v) for v in value]
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)                         # dates, times, intervals, UUIDs, ...


@app.get("/api/db/tables")
def db_tables(connector: str = "mysql"):
    c = get_connector(connector)
    # The servers' own row estimates: COUNT(*) would scan each table on the server Vectezium reads.
    if c.id == "mysql":
        with mysql() as conn, conn.cursor() as cur:
            # Otherwise MySQL serves estimates cached for up to a day; fresh ones still need no scan.
            cur.execute("SET SESSION information_schema_stats_expiry = 0")
            cur.execute("SELECT table_name AS name, table_rows AS `rows` FROM information_schema.tables "
                        "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
                        (c.schema,))
            result = [{"name": row["name"], "rows": row["rows"] or 0} for row in cur.fetchall()]
    else:
        with postgres() as conn:
            # Live tuples from the statistics, which follow every insert and delete.
            rows = conn.run("SELECT relname, n_live_tup FROM pg_stat_user_tables "
                            "WHERE schemaname = :schema ORDER BY relname", schema=c.schema)
        result = [{"name": name, "rows": count or 0} for name, count in rows]
    return {"database": c.database, "schema": c.schema, "tables": result, "estimated": True}


@app.get("/api/db/rows")
def db_rows(table: str, limit: int = 100, connector: str = "mysql"):
    c = get_connector(connector)
    limit = max(1, min(limit, 1000))
    if c.id == "mysql":
        with mysql() as conn, conn.cursor() as cur:
            cur.execute("SELECT table_name AS name FROM information_schema.tables "
                        "WHERE table_schema = %s", (c.schema,))
            if table not in {row["name"] for row in cur.fetchall()}:
                raise HTTPException(404, "no such table")
            cur.execute("SELECT column_name AS name FROM information_schema.key_column_usage "
                        "WHERE table_schema = %s AND table_name = %s AND constraint_name = 'PRIMARY' "
                        "ORDER BY ordinal_position", (c.schema, table))
            key = [row["name"] for row in cur.fetchall()]
            order = ", ".join(f"`{k}` DESC" for k in key) or "1"
            cur.execute(f"SELECT * FROM `{table}` ORDER BY {order} LIMIT %s", (limit,))
            rows = [{k: jsonable(v) for k, v in row.items()} for row in cur.fetchall()]
            columns = [d[0] for d in cur.description]
        return {"table": table, "key": key, "columns": columns, "rows": rows}

    ident = pg8000.native.identifier
    with postgres() as conn:
        found = conn.run("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                         "WHERE n.nspname = :schema AND c.relname = :table AND c.relkind IN ('r', 'p')",
                         schema=c.schema, table=table)
        if not found:
            raise HTTPException(404, "no such table")
        key = [name for (name,) in conn.run(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey) "
            "WHERE i.indrelid = :oid AND i.indisprimary "
            "ORDER BY array_position(i.indkey::int2[], a.attnum)", oid=found[0][0])]
        order = ", ".join(f"{ident(k)} DESC" for k in key) or "1"
        result = conn.run(f"SELECT * FROM {ident(c.schema)}.{ident(table)} ORDER BY {order} "
                          "LIMIT :limit", limit=limit)
        columns = [col["name"] for col in conn.columns]
    rows = [{k: jsonable(v) for k, v in zip(columns, row)} for row in result]
    return {"table": table, "key": key, "columns": columns, "rows": rows}


# ------------------------------------------------------------------------------- snapshots

# Vectezium takes these requests from files in its signal folder while it runs (Debezium's file
# signal channel): the UI writes one file per request to pending/; Vectezium applies it within a
# couple of seconds and moves it to processed/ with its result.
SIGNAL_TYPES = {
    "snapshot": "execute-snapshot",     # read the tables' rows again (also releases a held table)
    "stop": "stop-snapshot",            # drop tables (none given: all) from the snapshot queue
    "dismiss": "dismiss-held",          # mark held tables as snapshotted, without reading them
    "approve": "approve-resnapshot",    # approve the full re-snapshot Vectezium is waiting for
}
TABLE_NAME = re.compile(r"[A-Za-z0-9_$]{1,64}")


def capture_rules(c):
    """The table include and exclude lists of the connector's source in vectezium.yaml."""
    try:
        source = yaml.safe_load(VECTEZIUM_CONFIG.read_text())["sources"][c.id]
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return None
    return {key: source.get(key) or [] for key in ("table_include_list", "table_exclude_list")}


def captured_by_config(rules, name):
    """Whether the lists capture <schema>.<table>. As in Vectezium, a pattern must match the whole
    name, and an include list wins over an exclude list."""
    if rules["table_include_list"]:
        return any(re.fullmatch(p, name) for p in rules["table_include_list"])
    return not any(re.fullmatch(p, name) for p in rules["table_exclude_list"])


def read_signals(folder, newest=None):
    """The signal files in a folder, oldest first (or the newest `newest`, newest first)."""
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json") and not n.startswith("."))
    except FileNotFoundError:
        return []
    if newest:
        names = names[::-1][:newest]
    signals = []
    for name in names:
        try:
            body = json.loads((folder / name).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(body, dict):
            signals.append({**body, "name": name})
    return signals


@app.get("/api/snapshots")
def snapshots(connector: str = "mysql"):
    """Each table of the connector's schema with its snapshot state, from the published offset
    (so up to offset_publish_interval_secs old) and vectezium.yaml:

      not_captured   not in the source's include list
      new            in the include list but unknown to the running source: redeploy Vectezium
      held           its rows need reading, but Vectezium waits for an operator
      snapshotting   its rows are being read (`read`: rows so far)
      queued         waiting for the tables before it
      snapshotted    all its rows were read (or it was created while captured)
      unknown        nothing published yet
    """
    c = get_connector(connector)
    tables = db_tables(connector)["tables"]
    offset = published_offset(c)
    checkpoint = (offset or {}).get("checkpoint") or {}

    def names(refs):
        return [f"{db}.{table}" for db, table in refs or []]

    snapshotted = set(names(checkpoint.get("snapshotted_tables")))
    held = set(names(checkpoint.get("held_tables")))
    incremental = checkpoint.get("incremental") or {}
    queue = names(incremental.get("tables"))
    rules = capture_rules(c)

    result = []
    for t in tables:
        name = f"{c.schema}.{t['name']}"
        entry = {"name": t["name"], "rows": t["rows"]}
        if rules is not None and not captured_by_config(rules, name):
            entry["state"] = "not_captured"
        elif name in held:
            entry["state"] = "held"
        elif queue[:1] == [name]:
            entry.update(state="snapshotting", read=incremental.get("rows", 0))
        elif name in queue:
            entry.update(state="queued", position=queue.index(name))
        elif name in snapshotted:
            entry["state"] = "snapshotted"
        else:
            entry["state"] = "unknown" if offset is None else "new"
        result.append(entry)

    folder = SIGNALS / c.id
    try:
        waiting = json.loads((folder / "waiting.json").read_text())
    except (OSError, ValueError):
        waiting = None
    return {
        "schema": c.schema,
        "tables": result,
        "published_at": (offset or {}).get("committed_at"),
        "signals_enabled": (folder / "pending").is_dir(),
        "waiting": waiting,
        "pending": read_signals(folder / "pending"),
        "processed": read_signals(folder / "processed", newest=20),
    }


def same_origin(request: Request):
    """Refuses requests a browser sends on another site's behalf. The session cookie is
    SameSite=Strict already; this also covers browsers that ignore that."""
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(400, "cross-site request")
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
        raise HTTPException(400, "cross-origin request")
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "send JSON")


@app.post("/api/snapshots")
async def send_signal(request: Request):
    """Writes a signal for the connector's source: {"connector", "action", "tables"}, where
    `tables` are names in the connector's schema."""
    same_origin(request)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "send a JSON object")
    c = get_connector(body.get("connector", "mysql"))
    action = body.get("action")
    if action not in SIGNAL_TYPES:
        raise HTTPException(400, f"action: one of {', '.join(SIGNAL_TYPES)}")
    tables = body.get("tables") or []
    if not isinstance(tables, list) or not all(isinstance(t, str) and TABLE_NAME.fullmatch(t) for t in tables):
        raise HTTPException(400, "tables: a list of table names, without the schema")
    if action in ("snapshot", "dismiss") and not tables:
        raise HTTPException(400, f"{action} needs tables")
    if tables:
        existing = {t["name"] for t in (await asyncio.to_thread(db_tables, c.id))["tables"]}
        if missing := [t for t in tables if t not in existing]:
            raise HTTPException(404, f"no table {', '.join(missing)} in {c.schema}")
    pending = SIGNALS / c.id / "pending"
    if not pending.is_dir():
        raise HTTPException(503, f"no signal folder for {c.label}: Vectezium creates it when it "
                                 "starts with `signals` configured")

    sent_at = int(time.time() * 1000)
    signal = {"id": str(uuid.uuid4()), "type": SIGNAL_TYPES[action], "data": {},
              "sent_by": getattr(request.state, "user", None), "sent_at": sent_at}
    if tables:
        signal["data"]["data-collections"] = [f"{c.schema}.{t}" for t in dict.fromkeys(tables)]
    if action == "snapshot":
        signal["data"]["type"] = "incremental"
    # Named so the files sort in the order they were sent, and written under a name Vectezium
    # skips (a dot-file) before the rename, so it never reads one half-written.
    name = f"{sent_at:013d}-{signal['id']}.json"
    tmp = pending / f".{name}"
    tmp.write_text(json.dumps(signal, indent=2))
    os.replace(tmp, pending / name)
    return {**signal, "name": name}


# ------------------------------------------------------------------------------- licences

@app.get("/api/licences")
def licences():
    """The files in vectezium/: the MPL-2.0 text, the NOTICE and the crates the CDC sources add."""
    def read(name):
        try:
            return (LICENCES / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
    crates = read("LICENSE-3rdparty-cdc.csv")
    return {"notice": read("NOTICE"), "license": read("LICENSE"),
            "crates": list(csv.DictReader(crates.splitlines())) if crates else []}

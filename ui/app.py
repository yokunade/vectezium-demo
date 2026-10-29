"""The demo UI: live events as Vectezium captures them, the lake (Azurite) and the MySQL tables.

  /api/events          server-sent events: each line of Vectezium's live.ndjson as it is written
  /api/events/find     one event from live.ndjson by seq (the browser keeps large events truncated)
  /api/status          the published offset and the single-writer lock
  /api/lake            every blob under the connector's folder
  /api/lake/blob       one blob's content (gunzipped), parsed as NDJSON
  /api/mysql/tables    the shop tables and their row counts
  /api/mysql/rows      the first rows of one table
  /api/licences        Vectezium's licence, notice and the crates its patch adds
  /api/auth/*          sign-in, password change and sign-out (auth.py)

Everything except the sign-in page needs a session: see auth.py.
"""

import asyncio
import contextlib
import csv
import ctypes
import datetime
import decimal
import json
import mmap
import os
import zlib
from pathlib import Path

import pymysql
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobPrefix, BlobServiceClient
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import auth

EVENTS_FILE = Path(os.environ.get("EVENTS_FILE", "/events/live.ndjson"))
CONTAINER = os.environ.get("LAKE_CONTAINER", "raw")
ROOT = os.environ.get("LAKE_ROOT", "cdc/shop/")
DATABASE = os.environ.get("MYSQL_DATABASE", "shop")
BACKLOG_BYTES = 2_000_000
MAX_PREVIEW_LINES = 5000
LICENCES = Path(os.environ.get("LICENCES_DIR", "/licences"))

# live.ndjson is cut back to its newest LIVE_KEEP_BYTES once it grows past LIVE_MAX_BYTES, so it
# stays small however much is captured. A browser is sent at most STREAM_CHUNK_BYTES at a time,
# and one that falls more than MAX_LAG_BYTES behind skips ahead to the newest events.
LIVE_MAX_BYTES = int(os.environ.get("LIVE_MAX_BYTES", 64_000_000))
LIVE_KEEP_BYTES = int(os.environ.get("LIVE_KEEP_BYTES", 8_000_000))
STREAM_CHUNK_BYTES = 1_000_000
MAX_LAG_BYTES = 16_000_000


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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------------------- live events

class LiveFile:
    """Vectezium's live.ndjson, trimmed in place so it stays small.

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

live = LiveFile(EVENTS_FILE)


async def trim_live_file():
    while True:
        try:
            live.trim()
        except OSError as error:
            print(f"Could not trim {EVENTS_FILE}: {error}", flush=True)
        # Often: a burst of large rows can add ~100 MB a second (one stat call when nothing to do).
        await asyncio.sleep(1)


def sse(line):
    return f"data: {line.decode('utf-8', 'replace')}\n\n"


@app.get("/api/events")
async def events(request: Request, backlog: int = 500):
    """Streams live.ndjson: the last `backlog` lines, then every new line as it is written.

    Sends at most STREAM_CHUNK_BYTES per step, waiting for the browser between steps. A browser
    that falls more than MAX_LAG_BYTES behind (or behind the trimmed start of the file) skips
    ahead and gets a `gap` event with the number of bytes it missed.
    """

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
def find_event(seq: str):
    """The full event with this `seq` (not the published offset that shares it), if it is still
    in live.ndjson."""
    needle = f'"seq":"{seq}"'.encode()
    try:
        with open(EVENTS_FILE, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            at = m.rfind(needle)
            while at >= 0:
                line_start = m.rfind(b"\n", 0, at) + 1
                line_end = m.find(b"\n", at)
                event = json.loads(m[line_start:line_end if line_end >= 0 else len(m)])
                if "change_seq" not in event:
                    return event
                at = m.rfind(needle, 0, line_start)
    except (FileNotFoundError, ValueError):
        pass
    raise HTTPException(404, "no longer in the live file")


def split_lines(data):
    *complete, partial = data.split(b"\n")
    return [line for line in complete if line.strip()], partial


# ------------------------------------------------------------------------------- the lake

@app.get("/api/status")
def status():
    result = {"offset": None, "lock": None}
    try:
        text = lake.get_blob_client(ROOT + "_offsets/latest.json").download_blob().readall()
        result["offset"] = json.loads(text.splitlines()[0])
    except (ResourceNotFoundError, IndexError, ValueError):
        pass
    try:
        lease = lake.get_blob_client(ROOT + "_lock").get_blob_properties().lease
        result["lock"] = {"state": lease.state, "status": lease.status}
    except ResourceNotFoundError:
        pass
    return result


@app.get("/api/lake")
def lake_list(prefix: str = ""):
    """One folder of the connector's folder (`prefix`, relative to it): its subfolders and its
    files, newest first. Listing one level keeps each call small however large the lake gets."""
    if prefix and (not prefix.endswith("/") or ".." in prefix.split("/")):
        raise HTTPException(400, "prefix must be a folder such as shop/customers/")
    folders, files = [], []
    for item in lake.walk_blobs(name_starts_with=ROOT + prefix, delimiter="/"):
        if isinstance(item, BlobPrefix):
            folders.append(item.name[len(ROOT + prefix):].rstrip("/"))
        else:
            files.append({"name": item.name, "size": item.size,
                          "last_modified": item.last_modified.isoformat()})
    files.sort(key=lambda f: f["last_modified"], reverse=True)
    return {"container": CONTAINER, "root": ROOT, "prefix": prefix,
            "folders": sorted(folders), "files": files}


@app.get("/api/lake/blob")
def lake_blob(name: str):
    if not name.startswith(ROOT):
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


# ------------------------------------------------------------------------------- MySQL

def mysql():
    return pymysql.connect(host=os.environ.get("MYSQL_HOST", "mysql"),
                           user=os.environ.get("MYSQL_USER", "app"),
                           password=os.environ.get("MYSQL_PASSWORD", ""),
                           database=DATABASE, cursorclass=pymysql.cursors.DictCursor,
                           connect_timeout=5)


def jsonable(value):
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time, datetime.timedelta)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


@app.get("/api/mysql/tables")
def mysql_tables():
    # MySQL's own row estimates: COUNT(*) would scan each table on the server Vectezium reads.
    with mysql() as conn, conn.cursor() as cur:
        # Otherwise MySQL serves estimates cached for up to a day; fresh ones still need no scan.
        cur.execute("SET SESSION information_schema_stats_expiry = 0")
        cur.execute("SELECT table_name AS name, table_rows AS `rows` FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
                    (DATABASE,))
        result = [{"name": row["name"], "rows": row["rows"] or 0} for row in cur.fetchall()]
    return {"database": DATABASE, "tables": result, "estimated": True}


@app.get("/api/mysql/rows")
def mysql_rows(table: str, limit: int = 100):
    limit = max(1, min(limit, 1000))
    with mysql() as conn, conn.cursor() as cur:
        cur.execute("SELECT table_name AS name FROM information_schema.tables "
                    "WHERE table_schema = %s", (DATABASE,))
        if table not in {row["name"] for row in cur.fetchall()}:
            raise HTTPException(404, "no such table")
        cur.execute("SELECT column_name AS name FROM information_schema.key_column_usage "
                    "WHERE table_schema = %s AND table_name = %s AND constraint_name = 'PRIMARY' "
                    "ORDER BY ordinal_position", (DATABASE, table))
        key = [row["name"] for row in cur.fetchall()]
        order = ", ".join(f"`{c}` DESC" for c in key) or "1"
        cur.execute(f"SELECT * FROM `{table}` ORDER BY {order} LIMIT %s", (limit,))
        rows = [{k: jsonable(v) for k, v in row.items()} for row in cur.fetchall()]
        columns = [d[0] for d in cur.description]
    return {"table": table, "key": key, "columns": columns, "rows": rows}


# ------------------------------------------------------------------------------- licences

@app.get("/api/licences")
def licences():
    """The files in vectezium/: the MPL-2.0 text, the NOTICE and the crates the patch adds."""
    def read(name):
        try:
            return (LICENCES / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
    crates = read("LICENSE-3rdparty-mysql_cdc.csv")
    return {"notice": read("NOTICE"), "license": read("LICENSE"),
            "crates": list(csv.DictReader(crates.splitlines())) if crates else []}

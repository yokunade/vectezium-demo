"""Sends Vectezium a signal and waits for its result (scripts/snapshot.sh, while Vectezium runs).

  python send_signal.py <source> <db.table> [...]            snapshot these tables
  python send_signal.py <source> --all                       snapshot every table it has snapshotted
                                                             or holds; approve a waiting re-snapshot
  python send_signal.py <source> --dismiss <db.table> [...]  mark held tables as snapshotted
  python send_signal.py <source> --stop [<db.table> ...]     drop tables (none: all) from the queue
  python send_signal.py <source> --approve                   approve the full re-snapshot it waits for

<source> is the Vectezium source (`mysql`, `postgres`): its signal folder is /signals/<source>
(see `signals` in vectezium.yaml). The signal is a file in pending/; Vectezium applies it within a
couple of seconds and moves it to processed/ with its result. The UI's Snapshots tab writes the
same files.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

SIGNALS = Path(os.environ.get("SIGNALS_DIR", "/signals"))
OFFSETS = os.environ.get("OFFSETS_FILE", "/data/mysql/offsets.json")
WAIT_SECONDS = 20


def stored_tables():
    """The tables the stored offset lists as snapshotted or held."""
    try:
        with open(OFFSETS, encoding="utf-8") as f:
            offset = json.load(f)
    except (OSError, ValueError):
        sys.exit(f"cannot read {OFFSETS}: --all takes its tables from Vectezium's offset file")
    refs = (offset.get("snapshotted_tables") or []) + offset.get("held_tables", [])
    return list(dict.fromkeys(f"{db}.{table}" for db, table in refs))


def send(folder, kind, tables=None):
    signal = {"id": str(uuid.uuid4()), "type": kind, "data": {},
              "sent_by": "scripts/snapshot.sh", "sent_at": int(time.time() * 1000)}
    if tables:
        signal["data"]["data-collections"] = tables
    if kind == "execute-snapshot":
        signal["data"]["type"] = "incremental"
    name = f"{signal['sent_at']:013d}-{signal['id']}.json"
    tmp = folder / "pending" / f".{name}"
    tmp.write_text(json.dumps(signal, indent=2))
    os.replace(tmp, folder / "pending" / name)
    return name


def wait(folder, name):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            result = json.loads((folder / "processed" / name).read_text())["result"]
        except (OSError, ValueError, KeyError):
            time.sleep(0.5)
            continue
        return result
    return None


def main(args):
    if len(args) < 2:
        sys.exit(__doc__)
    source, *args = args
    folder = SIGNALS / source
    if not (folder / "pending").is_dir():
        sys.exit(f"{folder}/pending does not exist: is `signals` configured for the {source} source?")
    flags = {a for a in args if a.startswith("--")}
    tables = [a for a in args if not a.startswith("--")]
    if bad := [t for t in tables if t.count(".") != 1 or "/" in t]:
        sys.exit(f"expected <db>.<table>: {' '.join(bad)}")

    signals = []
    if "--approve" in flags or "--all" in flags:
        # Only applied while Vectezium waits for an approval; otherwise rejected, harmlessly.
        if (folder / "waiting.json").exists() or "--approve" in flags:
            signals.append(("approve-resnapshot", None))
    if "--dismiss" in flags:
        if not tables:
            sys.exit("--dismiss takes the held tables to mark as snapshotted")
        signals.append(("dismiss-held", tables))
    elif "--stop" in flags:
        signals.append(("stop-snapshot", tables))
    elif "--all" in flags:
        if not (folder / "waiting.json").exists():
            signals.append(("execute-snapshot", list(dict.fromkeys(stored_tables() + tables))))
    elif tables:
        signals.append(("execute-snapshot", tables))
    if not signals:
        sys.exit(__doc__)

    failed = False
    for kind, names in signals:
        name = send(folder, kind, names)
        print(f"Sent {kind}{' for ' + ', '.join(names) if names else ''} ({name})")
        result = wait(folder, name)
        if result is None:
            print(f"  no result after {WAIT_SECONDS} s: is Vectezium running? The signal stays in "
                  "pending/ and is applied when it is.")
            failed = True
        else:
            print(f"  {result['status']}: {result['message']}")
            failed |= result["status"] != "applied"
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv[1:])

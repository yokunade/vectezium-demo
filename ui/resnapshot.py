"""Queues tables for an ad-hoc incremental snapshot, or releases held snapshots.

  python resnapshot.py <db.table> [<db.table> ...]   snapshot these tables
  python resnapshot.py --all                         snapshot every table; also approves a full
                                                     re-snapshot after a purged binlog
  python resnapshot.py --dismiss <db.table> [...]    mark held tables as snapshotted, without
                                                     reading them

Vectezium records the captured tables whose rows it has snapshotted in `snapshotted_tables` of its
offset file. With `snapshot_new_tables: true`, it snapshots (incrementally, while streaming) every
captured table missing from that list when it starts. Removing a table from the list therefore
queues a fresh snapshot of it.

With `unplanned_snapshots: hold`, snapshots nobody asked for wait for an operator:
  - tables whose snapshot state was lost are listed in `held_tables` (streamed, not snapshotted).
    Queuing one here snapshots it; --dismiss marks it as complete instead.
  - after a purged binlog Vectezium refuses to start until `resnapshot_approved` is set (--all).

Vectezium must be stopped while the file is edited: it rewrites the file on every checkpoint.
"""

import json
import os
import sys

# Vectezium's data_dir is mounted at /data; each source keeps its state in a folder named after it.
OFFSETS = os.environ.get("OFFSETS_FILE", "/data/mysql/offsets.json")


def parse_tables(names):
    tables = []
    for name in names:
        db, _, table = name.partition(".")
        if not db or not table:
            sys.exit(f"{name}: expected <db>.<table>")
        tables.append([db, table])
    return tables


def main(args):
    if not args:
        sys.exit(__doc__)
    dismiss = args[0] == "--dismiss"
    everything = "--all" in args
    tables = parse_tables([a for a in args if a not in ("--all", "--dismiss")])
    if dismiss and (everything or not tables):
        sys.exit("--dismiss takes the held tables to mark as snapshotted")

    try:
        with open(OFFSETS, encoding="utf-8") as f:
            offset = json.load(f)
    except FileNotFoundError:
        sys.exit(f"{OFFSETS} does not exist: Vectezium has not stored an offset yet")
    done = offset.get("snapshotted_tables")
    if done is None:
        sys.exit("The offset file has no snapshotted_tables yet; let Vectezium run first")
    held = offset.get("held_tables", [])
    pending = [list(t) for t in (offset.get("incremental") or {}).get("tables", [])]

    changed = 0
    if dismiss:
        for t in tables:
            name = ".".join(t)
            if t in held:
                held.remove(t)
                done.append(t)
                changed += 1
                print(f"{name}: marked as snapshotted (its existing rows are not read)")
            else:
                print(f"{name}: not held")
    else:
        if everything:
            tables += [list(t) for t in done + held if list(t) not in tables]
            offset["resnapshot_approved"] = True
            changed += 1
            print("Approved a full re-snapshot if the binlog was purged")
        for t in tables:
            name = ".".join(t)
            if t in pending:
                print(f"{name}: already being snapshotted (or queued)")
            elif t in held:
                held.remove(t)
                changed += 1
                print(f"{name}: held snapshot released; it is snapshotted on the next start")
            elif t in done:
                done.remove(t)
                changed += 1
                print(f"{name}: queued for a snapshot")
            else:
                print(f"{name}: not in snapshotted_tables (not captured, or never snapshotted); "
                      "if it is captured, it is snapshotted on the next start anyway")

    if held:
        offset["held_tables"] = held
    else:
        offset.pop("held_tables", None)

    if changed:
        tmp = OFFSETS + ".resnapshot.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(offset, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, OFFSETS)


if __name__ == "__main__":
    main(sys.argv[1:])

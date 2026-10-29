#!/usr/bin/env bash
# Triggers an ad-hoc incremental snapshot of captured tables: their current rows are read again, in
# chunks, while changes keep streaming. Also releases snapshots Vectezium is holding.
#
#   ./scripts/snapshot.sh shop.products [shop.customers ...]   snapshot these (held or not)
#   ./scripts/snapshot.sh --all                 every table; approves a re-snapshot after a purge
#   ./scripts/snapshot.sh --dismiss shop.orders   mark a held table as snapshotted, no reading
#
# How: Vectezium does not take snapshot requests while running. It keeps the list of tables it has
# snapshotted in its offset file and, on start, snapshots every captured table missing from it. So
# this script stops Vectezium (which stores its last acknowledged offset and releases its lock),
# edits that list (and `held_tables`), and starts it again.
source "$(dirname "$0")/lib.sh"
require_running
if [ $# -eq 0 ]; then
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

say "Stopping Vectezium"
docker compose stop vectezium
trap 'say "Starting Vectezium"; docker compose start vectezium' EXIT

say "Updating its offset file"
docker compose run --rm --no-deps tools python resnapshot.py "$@"

trap - EXIT
say "Starting Vectezium"
docker compose start vectezium
if [ "$1" = "--dismiss" ]; then
  exit 0
fi
cat <<'EOF'

Watch the snapshot in the UI (http://localhost:8080): `snapshot` events (op r) arrive in chunks
of 250 rows, then a `snapshot completed` control event per table. In the lake they go to the same
table folders as other changes, with metadata.snapshot = "incremental".
Logs: docker compose logs -f vectezium
EOF

#!/usr/bin/env bash
# Triggers an ad-hoc incremental snapshot of captured tables: their current rows are read again, in
# chunks, while changes keep streaming. Also releases snapshots Vectezium is holding. The Snapshots
# tab in the UI does the same.
#
#   ./scripts/snapshot.sh shop.products [shop.customers ...]   snapshot these (held or not)
#   ./scripts/snapshot.sh --all                 every table; approves a re-snapshot after a purge
#   ./scripts/snapshot.sh --dismiss shop.orders   mark a held table as snapshotted, no reading
#   ./scripts/snapshot.sh --stop [shop.orders]  drop tables (none: all) from the snapshot queue
#   ./scripts/snapshot.sh --approve             approve the full re-snapshot Vectezium waits for
#   ./scripts/snapshot.sh --connector postgres public.products   the same for the PostgreSQL
#                                               connector (tables are <schema>.<table>)
#
# How: while Vectezium runs, it takes requests from its signal folder (`signals` in
# vectezium/vectezium.yaml): this script writes one there and prints Vectezium's answer. Nothing
# restarts. When Vectezium is stopped, the script edits its offset file instead (the list of
# tables it has snapshotted, `held_tables`), and the next start acts on it.
source "$(dirname "$0")/lib.sh"
require_running
if [ $# -eq 0 ]; then
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi
OFFSETS="/data/$SOURCE/offsets.json"

if [ -n "$(docker compose ps --status running --quiet vectezium 2>/dev/null)" ]; then
  say "Sending the request to Vectezium ($LABEL connector)"
  docker compose --progress quiet run --rm --no-deps -e OFFSETS_FILE="$OFFSETS" tools python send_signal.py "$SOURCE" "$@"
else
  case " $* " in
    *" --stop "* | *" --approve "*)
      echo "Vectezium is not running: --stop and --approve need it running." >&2
      exit 1 ;;
  esac
  say "Vectezium is not running: updating the $LABEL connector's offset file"
  docker compose --progress quiet run --rm --no-deps -e OFFSETS_FILE="$OFFSETS" tools python resnapshot.py "$@"
  echo "Start Vectezium to act on it: docker compose start vectezium"
fi
case " $* " in *" --dismiss "* | *" --stop "* | *" --approve "*) exit 0 ;; esac
cat <<EOF

Watch the snapshot in the UI (http://localhost:8080, Snapshots tab or Live events, connector
$LABEL): \`snapshot\` events (op r) arrive in chunks of 250 rows, then a \`snapshot completed\`
control event per table. In the lake they go to the same table folders as other changes, under
$LAKE$SCHEMA/, with metadata.snapshot = "incremental".
Logs: docker compose logs -f vectezium
EOF

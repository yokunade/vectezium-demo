#!/usr/bin/env bash
# Simulates losing a connector's state in Vectezium's data directory, as when it is redeployed to a
# new container without its old volume: stops Vectezium, deletes the connector's stored offset,
# and starts it again.
#
#   ./scripts/lose-offsets.sh                        the MySQL connector
#   ./scripts/lose-offsets.sh --connector postgres   the PostgreSQL connector
#
# With `offset_recovery`, Vectezium finds no stored offset and resumes from the offset it last
# published in the lake (_offsets/latest.json carries the whole stored offset as `checkpoint`):
# the same binlog position or LSN, the same seq numbering, and no new snapshot. Changes made after
# that published offset are read again with the seq they already had.
source "$(dirname "$0")/lib.sh"
require_running

say "Stopping Vectezium"
docker compose stop vectezium
trap 'say "Starting Vectezium"; docker compose start vectezium' EXIT

say "Deleting the $LABEL connector's stored offset (/var/lib/vectezium/$SOURCE/offsets.json)"
docker compose run --rm --no-deps tools rm -f "/data/$SOURCE/offsets.json"

trap - EXIT
say "Starting Vectezium"
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose start vectezium

# Show what it decided, once it is streaming (or has stopped with an error).
for _ in $(seq 30); do
  if grep -qE "$STREAMING|ERROR" <<<"$(source_logs "$started")"; then
    break
  fi
  sleep 1
done
source_logs "$started" | grep -E "offset|snapshot|Snapshot|ERROR" | cut -c1-400

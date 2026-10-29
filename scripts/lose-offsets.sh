#!/usr/bin/env bash
# Simulates losing Vectezium's data directory, as when it is redeployed to a new container without
# its old volume: stops Vectezium, deletes its stored offset, and starts it again.
#
#   ./scripts/lose-offsets.sh
#
# With `offset_recovery`, Vectezium finds no stored offset and resumes from the offset it last
# published in the lake (_offsets/latest.json carries the whole stored offset as `checkpoint`):
# the same binlog position, the same seq numbering, and no new snapshot. Changes made after that
# published offset are read again with the seq they already had.
source "$(dirname "$0")/lib.sh"
require_running

say "Stopping Vectezium"
docker compose stop vectezium
trap 'say "Starting Vectezium"; docker compose start vectezium' EXIT

say "Deleting its stored offset (/var/lib/vectezium/mysql/offsets.json)"
docker compose run --rm --no-deps tools rm -f /data/mysql/offsets.json

trap - EXIT
say "Starting Vectezium"
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose start vectezium

# Show what it decided, once it is streaming (or has stopped with an error).
for _ in $(seq 30); do
  if docker compose logs --since "$started" vectezium 2>&1 | grep -qE "Streaming changes|ERROR"; then
    break
  fi
  sleep 1
done
docker compose logs --since "$started" --no-log-prefix vectezium 2>&1 \
  | grep -E "offset|snapshot|Snapshot|ERROR" | cut -c1-400

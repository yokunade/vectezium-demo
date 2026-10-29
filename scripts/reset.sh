#!/usr/bin/env bash
# Deletes everything the demo created (MySQL data, the lake, Vectezium's offsets, the live event
# file) and removes the tables add-table.sh added to the config. The next
# `docker compose up -d` starts from the seeded database and a first snapshot.
source "$(dirname "$0")/lib.sh"

say "Removing the containers and volumes"
docker compose --profile tools down --volumes --remove-orphans

CONFIG=vectezium/vectezium.yaml
if grep -q "# added by scripts/add-table.sh" "$CONFIG"; then
  say "Removing added tables from $CONFIG"
  grep -v "# added by scripts/add-table.sh" "$CONFIG" > "$CONFIG.tmp"
  cat "$CONFIG.tmp" > "$CONFIG"
  rm -f "$CONFIG.tmp"
fi
say "Done. Start again with: docker compose up -d"

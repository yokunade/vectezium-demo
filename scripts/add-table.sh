#!/usr/bin/env bash
# Adds a new table to the capture, the way it is done in a real deployment: create the table, add it to
# the connector's table_include_list, redeploy.
#
#   ./scripts/add-table.sh [name]       default name: reviews
#
# 1. Creates shop.<name> with 300 rows. Vectezium ignores it: it is not in the include list.
# 2. Adds it to table_include_list in vectezium/vectezium.yaml.
# 3. Redeploys Vectezium (a new container with the new config). On start, Vectezium finds a
#    captured table it has never snapshotted and snapshots it incrementally (snapshot_new_tables),
#    while it keeps streaming the other tables.
source "$(dirname "$0")/lib.sh"
require_running

TABLE=${1:-reviews}
CONFIG=vectezium/vectezium.yaml
MARKER="# scripts/add-table.sh adds tables above this line"
if ! [[ $TABLE =~ ^[a-z][a-z0-9_]{0,40}$ ]]; then
  echo "Table name: lowercase letters, digits and underscores" >&2
  exit 1
fi
if grep -q "'shop\\\\.$TABLE'" "$CONFIG"; then
  echo "shop.$TABLE is already in $CONFIG" >&2
  exit 1
fi

say "1. Creating shop.$TABLE (not captured yet)"
sql <<SQL
CREATE TABLE IF NOT EXISTS $TABLE (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  product_id  INT NOT NULL,
  customer_id INT NOT NULL,
  rating      TINYINT NOT NULL,
  title       VARCHAR(100) NOT NULL,
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
);
INSERT INTO $TABLE (product_id, customer_id, rating, title)
WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 300)
SELECT 1 + (i * 7) % 120, 1 + (i * 13) % 600, 1 + (i * 3) % 5,
       ELT(1 + i % 5, 'Love it', 'Does the job', 'Not for me', 'Great value', 'Arrived late')
FROM n;
SELECT COUNT(*) AS rows_in_$TABLE FROM $TABLE;
SQL

say "2. Adding shop.$TABLE to table_include_list in $CONFIG"
awk -v marker="$MARKER" -v line="      - 'shop\\\\.$TABLE'   # added by scripts/add-table.sh" \
  'index($0, marker) { print line } { print }' "$CONFIG" > "$CONFIG.tmp"
cat "$CONFIG.tmp" > "$CONFIG"   # rewrite in place: the file is bind-mounted into the container
rm -f "$CONFIG.tmp"
grep -n -A7 "table_include_list:" "$CONFIG"

say "3. Redeploying Vectezium"
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose up -d --force-recreate --no-deps vectezium
for _ in $(seq 1 30); do
  if line=$(docker compose logs --no-log-prefix --since "$since" vectezium 2>&1 | grep -m1 "newly captured"); then
    echo "$line"
    break
  fi
  sleep 2
done
cat <<EOF

Watch shop.$TABLE in the UI (http://localhost:8080): a schema document, its 300 rows as
snapshot events, then a \`snapshot completed\` event; its files appear in the lake under
cdc/shop/shop/$TABLE/. Changes to it are captured from now on, e.g.:
  ./scripts/changes.sh sql "UPDATE $TABLE SET rating = 5 WHERE id = 1"
EOF

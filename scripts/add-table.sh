#!/usr/bin/env bash
# Adds a new table to the capture, the way it is done in a real deployment: create the table, add it to
# what the connector captures, redeploy. The same steps for both connectors.
#
#   ./scripts/add-table.sh [name]                        default name: reviews
#   ./scripts/add-table.sh --connector postgres [name]   in the PostgreSQL shop
#
# 1. Creates <schema>.<name> with 300 rows. Vectezium ignores it: it is not captured yet.
#    PostgreSQL: the table also gets REPLICA IDENTITY FULL and Vectezium's user SELECT on it (the
#    publication covers the whole schema, so it is published already).
# 2. Adds it to the connector's table_include_list in vectezium/vectezium.yaml.
# 3. Redeploys Vectezium (a new container with the current config). On start, Vectezium finds a
#    captured table it has never snapshotted and snapshots it incrementally (snapshot_new_tables),
#    while it keeps streaming the other tables.
source "$(dirname "$0")/lib.sh"
require_running

TABLE=${1:-reviews}
CONFIG=vectezium/vectezium.yaml
MARKER="# scripts/add-table.sh adds tables above this line"
TITLES="'Love it', 'Does the job', 'Not for me', 'Great value', 'Arrived late'"
if ! [[ $TABLE =~ ^[a-z][a-z0-9_]{0,40}$ ]]; then
  echo "Table name: lowercase letters, digits and underscores" >&2
  exit 1
fi
if grep -q "'$SCHEMA\\\\.$TABLE'" "$CONFIG"; then
  echo "$SCHEMA.$TABLE is already in $CONFIG" >&2
  exit 1
fi

say "1. Creating $SCHEMA.$TABLE (not captured yet)"
if [ "$CONNECTOR" = mysql ]; then
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
SELECT 1 + (i * 7) % 120, 1 + (i * 13) % 600, 1 + (i * 3) % 5, ELT(1 + i % 5, $TITLES)
FROM n;
SELECT COUNT(*) AS rows_in_$TABLE FROM $TABLE;
SQL
else
  sql <<SQL
CREATE TABLE IF NOT EXISTS $TABLE (
  id          serial PRIMARY KEY,
  product_id  int NOT NULL,
  customer_id int NOT NULL,
  rating      smallint NOT NULL,
  title       varchar(100) NOT NULL,
  created_at  timestamptz(3) NOT NULL DEFAULT now()
);
ALTER TABLE $TABLE REPLICA IDENTITY FULL;
GRANT SELECT ON $TABLE TO vectezium;
INSERT INTO $TABLE (product_id, customer_id, rating, title)
SELECT 1 + (i * 7) % 120, 1 + (i * 13) % 600, 1 + (i * 3) % 5, (ARRAY[$TITLES])[1 + i % 5]
FROM generate_series(1, 300) i;
SELECT COUNT(*) AS rows_in_$TABLE FROM $TABLE;
SQL
fi

say "2. Adding $SCHEMA.$TABLE to the $SOURCE source's table_include_list in $CONFIG"
# Above the marker line in this connector's source: each source has one.
awk -v marker="$MARKER" -v source="  $SOURCE:" -v line="      - '$SCHEMA\\\\.$TABLE'   # added by scripts/add-table.sh" \
  '/^  [a-z_]+:$/ { current = $0 } index($0, marker) && current == source { print line } { print }' \
  "$CONFIG" > "$CONFIG.tmp"
cat "$CONFIG.tmp" > "$CONFIG"   # rewrite in place: the file is bind-mounted into the containers
rm -f "$CONFIG.tmp"
awk -v source="  $SOURCE:" '/^  [a-z_]+:$/ { current = $0 }
  current == source && /table_include_list:/ { on = 1 } on { print } on && index($0, "add-table.sh adds") { on = 0 }' "$CONFIG"

say "3. Redeploying Vectezium"
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose up -d --force-recreate --no-deps vectezium
for _ in $(seq 1 30); do
  # MySQL logs "Snapshotting newly captured tables", PostgreSQL "… tables that were never snapshotted".
  logs=$(source_logs "$since")
  if line=$(grep -m1 -E "newly captured|never snapshotted" <<<"$logs"); then
    echo "$line"
    break
  fi
  sleep 2
done
cat <<EOF

Watch $SCHEMA.$TABLE in the UI (http://localhost:8080, connector $LABEL): a schema document,
its 300 rows as snapshot events, then a \`snapshot completed\` event; its files appear in the
lake under $LAKE$SCHEMA/$TABLE/. Changes to it are captured from now on, e.g.:
  ./scripts/changes.sh$CONNECTOR_FLAG sql "UPDATE $TABLE SET rating = 5 WHERE id = 1"
EOF

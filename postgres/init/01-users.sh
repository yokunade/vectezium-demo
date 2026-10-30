#!/bin/bash
# Runs once, when the PostgreSQL volume is first created.
set -euo pipefail

# Vectezium's CDC user: REPLICATION plus SELECT on the captured tables (granted in 02-shop.sql),
# nothing else. The application user the demo commands use owns the tables.
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
CREATE ROLE vectezium WITH LOGIN REPLICATION PASSWORD 'vectezium-demo-pw';
CREATE ROLE app WITH LOGIN PASSWORD 'app-demo-pw';
GRANT CREATE ON SCHEMA public TO app;
SQL

# The official image's pg_hba.conf has no line for replication connections from other hosts.
echo "host replication vectezium all scram-sha-256" >> "$PGDATA/pg_hba.conf"

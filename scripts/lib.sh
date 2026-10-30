# Shared by the demo scripts (sourced, not run).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Git Bash on Windows would rewrite container paths such as /data/offsets.json.
export MSYS_NO_PATHCONV=1

say() { printf '\033[1m%s\033[0m\n' "$*"; }

# The connector a script works on: `--connector mysql|postgres` (or `--mysql`, `--postgres`,
# `--pg`) anywhere in its arguments, else $CONNECTOR, else mysql. The option is taken out of the
# script's arguments ("$@"), so the scripts only see their own.
CONNECTOR=${CONNECTOR:-mysql}
args=()
while (($#)); do
  case $1 in
    --connector) CONNECTOR=${2:?"--connector takes mysql or postgres"}; shift 2 ;;
    --connector=*) CONNECTOR=${1#*=}; shift ;;
    --mysql) CONNECTOR=mysql; shift ;;
    --postgres | --pg) CONNECTOR=postgres; shift ;;
    *) args+=("$1"); shift ;;
  esac
done
set -- ${args[@]+"${args[@]}"}
unset args

# Per connector: the compose service, the schema its tables are named in (<schema>.<table> in
# events and in the lake), its folder in the lake, and the Vectezium source (also the name of its
# folder in Vectezium's data_dir).
case $CONNECTOR in
  mysql)
    LABEL=MySQL SERVICE=mysql SCHEMA=shop LAKE=cdc/shop/ SOURCE=mysql ;;
  postgres | postgresql | pg)
    CONNECTOR=postgres LABEL=PostgreSQL SERVICE=postgres SCHEMA=public LAKE=cdc/shop_pg/ SOURCE=postgres ;;
  *)
    echo "Unknown connector '$CONNECTOR': mysql or postgres" >&2
    exit 1 ;;
esac
# How to name it on a command line, for the hints the scripts print.
CONNECTOR_FLAG=$([ "$CONNECTOR" = mysql ] && echo "" || echo " --connector $CONNECTOR")

mysql_app() {
  docker compose exec -T -e MYSQL_PWD=app-demo-pw mysql mysql --user=app --database=shop "$@"
}

psql_app() {
  docker compose exec -T -e PGPASSWORD=app-demo-pw postgres psql --no-psqlrc --username=app --dbname=shop "$@"
}

# As the superuser, for what the application user may not do (the publication is the DBA's).
psql_admin() {
  docker compose exec -T postgres psql --no-psqlrc --username=postgres --dbname=shop "$@"
}

# Runs SQL from stdin against the shop database as the application user, echoing it first.
sql() {
  local statements
  statements=$(cat)
  printf '\033[2m%s\033[0m\n' "$statements"
  if [ "$CONNECTOR" = mysql ]; then
    printf '%s\n' "$statements" | mysql_app --table
  else
    printf '%s\n' "$statements" | psql_app --set=ON_ERROR_STOP=1
  fi
}

# Runs one query and prints the bare result, columns separated by tabs.
query() {
  if [ "$CONNECTOR" = mysql ]; then
    mysql_app --batch --skip-column-names -e "$1"
  else
    psql_app --no-align --tuples-only --field-separator=$'\t' -c "$1"
  fi
}

# Runs SQL from stdin, carrying on past statements that fail, and prints only the errors.
sql_batch() {
  if [ "$CONNECTOR" = mysql ]; then
    mysql_app --force 2>&1 >/dev/null | grep '^ERROR' || true
  else
    psql_app --quiet 2>&1 >/dev/null | grep 'ERROR' || true
  fi
}

require_running() {
  if [ -z "$(docker compose ps --status running --quiet "$SERVICE" 2>/dev/null)" ]; then
    echo "The demo is not running. Start it with: docker compose up -d --build" >&2
    exit 1
  fi
}

# The message each source logs once it streams changes.
STREAMING=$([ "$CONNECTOR" = mysql ] && echo "Streaming changes from the binlog" ||
  echo "Streaming changes from the replication slot")

# Prints what Vectezium logged since $1 (an RFC 3339 time) for this connector's source: the lines
# in its span (component_id=<source>), or every line if none are.
source_logs() {
  local logs ours
  logs=$(docker compose logs --no-log-prefix --since "$1" vectezium 2>&1 || true)
  ours=$(grep -E "component_id=$SOURCE[ }]" <<<"$logs" || true)
  printf '%s\n' "${ours:-$logs}"
}

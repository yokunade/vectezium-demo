# Shared by the demo scripts (sourced, not run).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Git Bash on Windows would rewrite container paths such as /data/offsets.json.
export MSYS_NO_PATHCONV=1

say() { printf '\033[1m%s\033[0m\n' "$*"; }

mysql_app() {
  docker compose exec -T -e MYSQL_PWD=app-demo-pw mysql mysql --user=app --database=shop "$@"
}

# Runs SQL from stdin against the shop database as the application user, echoing it first.
sql() {
  local statements
  statements=$(cat)
  printf '\033[2m%s\033[0m\n' "$statements"
  printf '%s\n' "$statements" | mysql_app --table
}

# Runs one query and prints the bare result.
query() {
  mysql_app --batch --skip-column-names -e "$1"
}

require_running() {
  if [ -z "$(docker compose ps --status running --quiet mysql 2>/dev/null)" ]; then
    echo "The demo is not running. Start it with: docker compose up -d --build" >&2
    exit 1
  fi
}

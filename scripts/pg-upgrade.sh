#!/usr/bin/env bash
# Move World Assistant's database from PostgreSQL 16 (PostGIS 3.4) to PostgreSQL 18 (PostGIS 3.6).
#
#   bash scripts/pg-upgrade.sh              upgrade (says what it will do and asks first)
#   bash scripts/pg-upgrade.sh --yes        upgrade without asking
#   bash scripts/pg-upgrade.sh --rollback   go back to 16 and the old data folder, after an upgrade that worked
#
# A new major version of PostgreSQL cannot open the old one's data folder, so the data is copied across:
#
#   1. a World Assistant backup, then everything but the database is stopped (nothing writes from here on)
#   2. every table's row count is noted, and the whole database is dumped (pg_dump) into the backups folder
#   3. PostgreSQL 18 starts on a NEW folder (WA_DATA_DIR/pg18) and the dump is loaded into it
#   4. the row counts are compared, then everything starts again and every page is checked
#
# The old folder (WA_DATA_DIR/pgdata) is never changed. If any step fails, .env is put back, PostgreSQL 16
# starts on the old folder and nothing is lost. Delete the old folder yourself once you are happy (a week, say).
# --rollback does the same switch back by hand; anything saved since the upgrade is then not in the old folder.
#
# Needs free space for about twice the database (the dump and the new folder); it checks first.
set -euo pipefail
cd "$(dirname "$0")/.."

NEW_TAG=18-3.6
NEW_DIR=pg18
NEW_MOUNT=/var/lib/postgresql
DB=worldassistant

YES=0
MODE=upgrade
for arg in "$@"; do
  case "$arg" in
    --yes | -y) YES=1 ;;
    --rollback) MODE=rollback ;;
    -h | --help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)"; exit 2 ;;
  esac
done

[ -f .env ] || { echo "There is no .env here: run this from the World Assistant folder."; exit 1; }
set -a
# shellcheck disable=SC1091
. ./.env
set +a
DATA="${WA_DATA_DIR:-/mnt/user/appdata/world-assistant}"
BACKUPS="$DATA/backups"
ENV_SAVED=".env.before-pg18"

env_set() { sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
env_del() { sed -i "/^$1=/d" .env; }
sql() { docker compose exec -T wa-db psql -U "$DB" -d "${2:-$DB}" -v ON_ERROR_STOP=1 -tAc "$1"; }
ask() {
  [ "$YES" = 1 ] && return 0
  local reply
  read -r -p "$1 [y/N] " reply
  case "$reply" in y | Y | yes | YES) return 0 ;; *) echo "Nothing was changed."; exit 1 ;; esac
}
start_all() {
  if [ -n "${WA_IMAGE:-}" ]; then docker compose up -d --no-build; else docker compose up -d; fi
}
others() { docker compose config --services | grep -vx wa-db || true; }
db_ready() {  # wait for the database to answer; $1: the major version it must be
  for _ in $(seq 1 90); do
    # over TCP first: on its first start the image runs a temporary server on the socket only, then restarts
    if ! docker compose exec -T wa-db pg_isready -q -h 127.0.0.1 -U "$DB" 2>/dev/null; then sleep 2; continue; fi
    v="$(sql "show server_version_num" 2>/dev/null | tr -d '[:space:]' || true)"
    if [ -n "$v" ] && [ "${v:0:2}" = "$1" ]; then return 0; fi
    sleep 2
  done
  return 1
}
api_ok() {
  for _ in $(seq 1 60); do
    if docker compose exec -T api python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}
counts() {  # every table in public and its exact row count, sorted
  local t
  for t in $(sql "select tablename from pg_tables where schemaname = 'public' order by 1"); do
    printf '%s %s\n' "$t" "$(sql "select count(*) from public.\"$t\"" | tr -d '[:space:]')"
  done
}

if [ "$MODE" = rollback ]; then
  case "${WA_PG_TAG:-}" in
    18*) ;;
    *) echo "The database is not on PostgreSQL 18 here (WA_PG_TAG is not set to 18), so there is nothing to roll back."; exit 1 ;;
  esac
  [ -d "$DATA/pgdata" ] || { echo "The old data folder ($DATA/pgdata) is gone, so there is nothing to go back to."; exit 1; }
  echo "This goes back to PostgreSQL 16 and the old data folder ($DATA/pgdata)."
  echo "Anything World Assistant saved since the upgrade is not in that folder and will not be there."
  ask "Go back to PostgreSQL 16?"
  docker compose stop
  env_del WA_PG_TAG; env_del WA_PG_DIR; env_del WA_PG_MOUNT
  unset WA_PG_TAG WA_PG_DIR WA_PG_MOUNT
  start_all
  db_ready 16 || { echo "PostgreSQL 16 did not start. Look at: docker compose logs --tail 40 wa-db"; exit 1; }
  echo "Back on PostgreSQL 16. The PostgreSQL 18 folder ($DATA/$NEW_DIR) is still there; delete it when you no longer need it."
  exit 0
fi

# --- upgrade ---------------------------------------------------------------------------------------------------
case "${WA_PG_TAG:-}" in
  18*) echo "Already on PostgreSQL 18 (WA_PG_TAG=$WA_PG_TAG in .env). Nothing to do."; exit 0 ;;
esac
[ -n "$(docker compose ps --status running -q wa-db 2>/dev/null)" ] || {
  echo "The database is not running. Start World Assistant first (docker compose up -d), then run this again."; exit 1; }
version="$(sql "show server_version_num" | tr -d '[:space:]')"
[ "${version:0:2}" = 16 ] || { echo "The database is PostgreSQL ${version:0:2}, not 16: this script only moves 16 to 18."; exit 1; }
if [ -e "$DATA/$NEW_DIR" ] && [ -n "$(ls -A "$DATA/$NEW_DIR" 2>/dev/null)" ]; then
  echo "$DATA/$NEW_DIR already has files in it (an earlier attempt?). Move or delete it, then run this again."
  exit 1
fi
size="$(sql "select pg_database_size('$DB')" | tr -d '[:space:]')"
mkdir -p "$BACKUPS"
free_kb="$(df -Pk "$DATA" | awk 'NR == 2 {print $4}')"
need_kb=$(( size * 5 / 2 / 1024 ))
if [ "$free_kb" -lt "$need_kb" ]; then
  echo "Not enough free space in $DATA: the database is $(( size / 1048576 )) MB and this needs about $(( need_kb / 1024 )) MB free; there is $(( free_kb / 1024 )) MB."
  exit 1
fi

echo "This moves World Assistant's database ($(( size / 1048576 )) MB) from PostgreSQL 16 to 18:"
echo "  - World Assistant stops for a few minutes (the database copy and checks)"
echo "  - a full copy goes into $BACKUPS, and PostgreSQL 18 uses a new folder, $DATA/$NEW_DIR"
echo "  - the old folder, $DATA/pgdata, is not changed; if anything fails it goes back to it by itself"
ask "Go ahead?"

stamp="$(date +%Y%m%d-%H%M%S)"
dump="$BACKUPS/wa-$stamp-pg16.dump"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if [ -n "$(docker compose ps --status running -q api 2>/dev/null)" ]; then
  echo "Backing up..."
  docker compose exec -T api python -m app.backup create || { echo "The backup failed, so nothing has been changed."; exit 1; }
fi

echo "Stopping World Assistant (the database stays up)..."
# shellcheck disable=SC2046
docker compose stop $(others)

restart_old() {  # anything failed before the switch: start everything again as it was
  echo "Starting World Assistant again on PostgreSQL 16; nothing was changed."
  start_all || true
  exit 1
}

echo "Counting rows..."
counts > "$work/before" || restart_old
schema="$(sql "select version_num from alembic_version" | tr -d '[:space:]')" || restart_old

echo "Copying the database to $dump ..."
if ! docker compose exec -T wa-db pg_dump -U "$DB" -Fc "$DB" > "$dump" || [ ! -s "$dump" ]; then
  rm -f "$dump"
  echo "The copy failed."
  restart_old
fi
docker compose exec -T wa-db pg_restore -l < "$dump" > /dev/null || { echo "The copy cannot be read back."; restart_old; }
echo "  $(( $(stat -c %s "$dump") / 1048576 )) MB, $(wc -l < "$work/before") tables, schema $schema"

# --- the switch: from here on, a failure puts .env back and starts 16 on the old folder -----------------------------
cp .env "$ENV_SAVED"
roll_back() {
  echo
  echo "The upgrade did not finish ($1). Going back to PostgreSQL 16 and the old data folder; nothing is lost."
  docker compose stop || true
  cp "$ENV_SAVED" .env
  unset WA_PG_TAG WA_PG_DIR WA_PG_MOUNT
  start_all || true
  if db_ready 16; then echo "Back on PostgreSQL 16. $DATA/$NEW_DIR is left for a look; delete it before trying again."
  else echo "PostgreSQL 16 did not come back by itself. Look at: docker compose logs --tail 40 wa-db"; fi
  exit 1
}
mkdir -p "$DATA/$NEW_DIR"
env_set WA_PG_TAG "$NEW_TAG"
env_set WA_PG_DIR "$NEW_DIR"
env_set WA_PG_MOUNT "$NEW_MOUNT"
export WA_PG_TAG="$NEW_TAG" WA_PG_DIR="$NEW_DIR" WA_PG_MOUNT="$NEW_MOUNT"

echo "Starting PostgreSQL 18 on $DATA/$NEW_DIR ..."
docker compose pull wa-db || echo "Could not pull postgis/postgis:$NEW_TAG; using it if it is already here."
docker compose up -d --no-deps wa-db || roll_back "PostgreSQL 18 would not start"
db_ready 18 || roll_back "PostgreSQL 18 did not become ready"

echo "Loading the copy..."
# A fresh, empty database: the image's own one has extensions (the tiger geocoder) World Assistant removed.
sql "drop database $DB with (force)" postgres >/dev/null || roll_back "could not clear the new database"
sql "create database $DB owner $DB template template0" postgres >/dev/null || roll_back "could not create the new database"
docker compose exec -T wa-db pg_restore -U "$DB" -d "$DB" --exit-on-error --single-transaction --no-owner < "$dump" \
  || roll_back "loading the copy failed"
sql "analyze" >/dev/null || true
[ "${WA_PGUP_FAIL_AT:-}" = loaded ] && roll_back "a failure asked for by the test (WA_PGUP_FAIL_AT)"

echo "Checking every table's rows..."
counts > "$work/after" || roll_back "could not count the rows"
if ! diff "$work/before" "$work/after" > "$work/diff"; then
  cat "$work/diff"
  roll_back "the row counts differ"
fi
[ "$(sql "select version_num from alembic_version" | tr -d '[:space:]')" = "$schema" ] || roll_back "the schema version differs"
echo "  all $(wc -l < "$work/after") tables match; PostGIS $(sql "select postgis_lib_version()" | tr -d '[:space:]')"

echo "Starting World Assistant..."
start_all || roll_back "World Assistant would not start"
api_ok || roll_back "the api did not become healthy"
echo "Checking every page and endpoint..."
docker compose exec -T api python -m app.smoke || roll_back "some pages failed"

rm -f "$ENV_SAVED"
echo
echo "Done: World Assistant is on PostgreSQL 18 (PostGIS 3.6)."
echo "  The old data folder, $DATA/pgdata, is kept. When you are happy (after a week, say), delete it:"
echo "      rm -rf '$DATA/pgdata'"
echo "  Until then, 'bash scripts/pg-upgrade.sh --rollback' goes back to it (losing anything saved since)."
echo "  The copy taken before the upgrade is $dump; it can go too."

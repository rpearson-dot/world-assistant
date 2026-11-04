#!/usr/bin/env bash
# CI: the move from PostgreSQL 16 to 18 (scripts/pg-upgrade.sh) on a real stack, as on the NAS.
#
#   1. a stack on PostgreSQL 16 (an existing install: no WA_PG_* in .env), with data in it
#   2. an upgrade that fails after loading the copy goes back to 16 by itself, data intact
#   3. the upgrade: PostgreSQL 18, every row there, every page answers, the old folder untouched
#   4. --rollback goes back to 16 and the old folder
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && { echo ".env exists; this script is for a clean checkout (CI)"; exit 2; }
WORK="$(mktemp -d)"
DATA="$WORK/data"
PORT="${WA_API_PORT:-18011}"
cat > .env <<ENV
WA_SECRETS_DIR=$WORK/secrets
WA_DATA_DIR=$DATA
WA_API_PORT=$PORT
COMPOSE_PROJECT_NAME=wa-pgup
COMPOSE_FILE=docker-compose.yml
WA_NH_POLL_ENABLED=false
WA_NH_CAPTURE_ENABLED=false
WA_FUEL_POLL_ENABLED=false
WA_NAPTAN_POLL_ENABLED=false
WA_FLOOD_POLL_ENABLED=false
WA_CODEPOINT_POLL_ENABLED=false
WA_BOUNDARY_POLL_ENABLED=false
WA_USRN_POLL_ENABLED=false
WA_ENERGY_POLL_ENABLED=false
WA_TFLROADS_POLL_ENABLED=false
ENV
unset WA_IMAGE WA_PG_TAG WA_PG_DIR WA_PG_MOUNT
cleanup() {
  local status=$?
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f .env .env.before-pg18
  rm -rf "$WORK" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT
fail() { echo "::error title=postgres upgrade::$*"; docker compose ps -a || true; docker compose logs --tail 40 wa-db api migrate || true; exit 1; }
step() { printf "\n== %s\n" "$*"; }

mkdir -p "$DATA/backups" "$DATA/keys"
chmod 777 "$DATA/backups" "$DATA/keys"
bash scripts/init-secrets.sh >/dev/null
API="http://127.0.0.1:$PORT"
TOKEN="$(cat "$WORK/secrets/api_token.txt")"
auth=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")
major() { docker compose exec -T wa-db psql -U worldassistant -d worldassistant -tAc "show server_version_num" | tr -d '[:space:]' | cut -c1-2; }
has_place() { curl -fsS "${auth[@]}" "$API/v1/places" | grep -q "\"$1\""; }

step "a stack on PostgreSQL 16, with data"
docker compose up -d --build || fail "the stack would not start"
for _ in $(seq 1 60); do curl -fsS "$API/healthz" >/dev/null 2>&1 && break; sleep 3; done
[ "$(major)" = 16 ] || fail "expected PostgreSQL 16 to start with"
docker compose exec -T api python -m app.smoke --seed >/dev/null || fail "smoke on 16"
curl -fsS -X PUT "${auth[@]}" -d '{"name":"Before","kind":"other","lat":51.5,"lon":-0.1}' "$API/v1/places/before" >/dev/null

step "a failed upgrade goes back to 16 by itself"
if WA_PGUP_FAIL_AT=loaded bash scripts/pg-upgrade.sh --yes; then fail "the failure was not reported"; fi
[ "$(major)" = 16 ] || fail "not back on 16 after a failure"
grep -q '^WA_PG_TAG=' .env && fail ".env was not put back"
for _ in $(seq 1 60); do curl -fsS "$API/healthz" >/dev/null 2>&1 && break; sleep 3; done
has_place before || fail "the data is not there after going back"
docker compose stop >/dev/null
rm -rf "$DATA/pg18"  # as the message says: delete it before trying again
docker compose up -d >/dev/null
for _ in $(seq 1 60); do curl -fsS "$API/healthz" >/dev/null 2>&1 && break; sleep 3; done

step "the upgrade"
bash scripts/pg-upgrade.sh --yes || fail "the upgrade failed"
[ "$(major)" = 18 ] || fail "not on 18 after the upgrade"
grep -qx 'WA_PG_TAG=18-3.6' .env || fail ".env does not say 18"
[ -n "$(ls -A "$DATA/pgdata")" ] || fail "the old folder should be untouched"
has_place before || fail "the place did not come across"
docker compose exec -T wa-db psql -U worldassistant -d worldassistant -tAc "select postgis_lib_version()"
curl -fsS -X PUT "${auth[@]}" -d '{"name":"After","kind":"other","lat":51.6,"lon":-0.2}' "$API/v1/places/after" >/dev/null \
  || fail "cannot write on 18"
docker compose exec -T api python -m app.smoke >/dev/null || fail "smoke on 18"
bash scripts/pg-upgrade.sh --yes | grep -q "Already on PostgreSQL 18" || fail "a second run should do nothing"

step "--rollback"
bash scripts/pg-upgrade.sh --rollback --yes || fail "the rollback failed"
[ "$(major)" = 16 ] || fail "not on 16 after --rollback"
for _ in $(seq 1 60); do curl -fsS "$API/healthz" >/dev/null 2>&1 && break; sleep 3; done
has_place before || fail "the data before the upgrade should be there"
if has_place after; then fail "a place saved on 18 cannot be in the old folder"; fi
echo "postgres upgrade: all checks passed"

#!/usr/bin/env bash
# Whole-stack check, run by CI (and runnable on any machine with Docker):
# brings the real compose stack up on PostGIS with throwaway secrets, then proves it works.
#
#   1. every service starts, migrations run on PostGIS, the api becomes healthy
#   2. every page and every GET endpoint answers without a 500 (python -m app.smoke --seed)
#   3. the last migration goes down and up again
#   4. a backup, a deletion and a restore bring the data back
#   5. (CI) the pages load in a real browser without script errors: scripts/page-check.py
#   6. Home Assistant sensors reach the bundled MQTT broker, which refuses anonymous clients
#   7. keys saved from the Settings page take effect; the graph explorer's questions run on Neo4j
#
# Nothing here touches the internet APIs: all pollers are switched off.
set -euo pipefail
cd "$(dirname "$0")/.."

WORK="$(mktemp -d)"
export WA_SECRETS_DIR="$WORK/secrets" WA_DATA_DIR="$WORK/data"
export WA_MQTT_HOST=wa-mqtt WA_MQTT_USER=wa WA_MQTT_PUBLISH_PORT=18830 WA_API_PORT="${WA_API_PORT:-18010}" COMPOSE_PROJECT_NAME=wa-ci
export WA_NH_POLL_ENABLED=false WA_NH_CAPTURE_ENABLED=false WA_FUEL_POLL_ENABLED=false WA_NAPTAN_POLL_ENABLED=false WA_FLOOD_POLL_ENABLED=false WA_CODEPOINT_POLL_ENABLED=false WA_BOUNDARY_POLL_ENABLED=false WA_USRN_POLL_ENABLED=false WA_ENERGY_POLL_ENABLED=false WA_TFLROADS_POLL_ENABLED=false
export COMPOSE_FILE=docker-compose.yml:docker-compose.mqtt.yml:docker-compose.graph.yml
unset WA_IMAGE WA_REDIS_URL

mkdir -p "$WA_DATA_DIR/backups" "$WA_DATA_DIR/pgdata"
chmod 777 "$WA_DATA_DIR/backups"
mkdir -p "$WA_DATA_DIR/keys" && chmod 777 "$WA_DATA_DIR/keys"
# init-secrets.sh sources .env if there is one; a stray one must not redirect the directory.
[ -f .env ] && { echo ".env exists; this script is for a clean checkout (CI)"; exit 2; }
bash scripts/init-secrets.sh >/dev/null

CURRENT="start"
# Also publish the tail of the logs as annotations, which can be read without downloading the job log.
annotate() {
  local text
  text="$(tail -n "${2:-25}" | tr -d '\r' | cut -c1-300 | sed ':a;N;$!ba;s/%/%25/g;s/\n/%0A/g')"
  echo "::error title=stack check: $1::${text:0:3500}"
}
dump_and_fail() {
  echo "::group::compose logs"
  docker compose ps -a || true
  docker compose logs --tail 80 || true
  echo "::endgroup::"
  { echo "failed during: $CURRENT"; docker compose ps -a 2>&1 | tail -12; docker compose logs --tail 12 api migrate wa-db 2>&1; } | annotate "$CURRENT" 45
  exit 1
}
cleanup() {
  local status=$?
  trap - ERR
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  # Files the containers created belong to other users; leave them to the runner if rm cannot.
  rm -rf "$WORK" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT
trap 'echo "::error title=script error::line $LINENO during: $CURRENT: $BASH_COMMAND"' ERR

step() { CURRENT="$*"; printf "\n== %s\n" "$*"; echo "::notice title=stack step::$*"; }
API="http://127.0.0.1:$WA_API_PORT"
TOKEN="$(cat "$WA_SECRETS_DIR/api_token.txt")"

step "start the stack"
docker compose up -d --build || dump_and_fail
for _ in $(seq 1 60); do
  if curl -fsS "$API/healthz" >/dev/null 2>&1; then break; fi
  sleep 3
done
curl -fsS "$API/healthz" >/dev/null || dump_and_fail
code="$(docker compose ps -a --format '{{.ExitCode}}' migrate | head -1)"
[ "$code" = "0" ] || { echo "migrate exited with $code"; dump_and_fail; }
docker compose exec -T wa-db psql -U worldassistant -d worldassistant -tAc \
  "select postgis_version()" || dump_and_fail

step "every page and endpoint"
if ! docker compose exec -T api python -m app.smoke --seed | tee "$WORK/smoke.txt"; then
  grep -E "^FAIL|checks passed" "$WORK/smoke.txt" | annotate "smoke failures" 30
  dump_and_fail
fi

step "the latest migration goes down and up"
docker compose run --rm --no-deps migrate alembic downgrade -1 || dump_and_fail
docker compose run --rm --no-deps migrate alembic upgrade head || dump_and_fail

step "backup, delete, restore"
auth=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")
curl -fsS -X PUT "${auth[@]}" -d '{"name":"Round trip","kind":"other","lat":51.5,"lon":-0.1}' \
  "$API/v1/places/roundtrip" >/dev/null || dump_and_fail
name="$(docker compose exec -T api python -m app.backup create | sed -n 's/^wrote \([^ ]*\) .*/\1/p')"
[ -n "$name" ] || { echo "backup did not report a file name"; dump_and_fail; }
curl -fsS -X DELETE "${auth[@]}" "$API/v1/places/roundtrip" >/dev/null || dump_and_fail
if curl -fsS "${auth[@]}" "$API/v1/places" | grep -q '"roundtrip"'; then
  echo "the place should be gone before the restore"; dump_and_fail
fi
docker compose stop worker >/dev/null
docker compose exec -T api python -m app.backup restore "$name" --force || dump_and_fail
docker compose start worker >/dev/null
curl -fsS "${auth[@]}" "$API/v1/places" | grep -q '"roundtrip"' || { echo "restore did not bring the place back"; dump_and_fail; }

step "pages in a real browser"
if command -v python3 >/dev/null && python3 -c "import playwright" 2>/dev/null; then
  WA_SMOKE_URL="$API" WA_SMOKE_TOKEN="$TOKEN" python3 scripts/page-check.py || dump_and_fail
else
  echo "playwright is not installed here; skipped"
fi

step "Home Assistant sensors over the bundled broker"
PW="$(cat "$WA_SECRETS_DIR/mqtt_password.txt")"
[ -n "$PW" ] || { echo "init-secrets.sh should generate the bundled broker's password"; dump_and_fail; }
for _ in $(seq 1 20); do
  [ "$(docker compose ps --format '{{.Health}}' wa-mqtt)" = "healthy" ] && break
  sleep 3
done
docker compose exec -T worker python -c "
import asyncio
from app import ha_publish
n = asyncio.run(ha_publish.ha_sync({}))
print(n, 'sensors published')
raise SystemExit(0 if n > 0 else 1)" || dump_and_fail
docker compose exec -T wa-mqtt mosquitto_sub -h 127.0.0.1 -u wa -P "$PW" -t 'homeassistant/#' -C 1 -W 10 >/dev/null \
  || { echo "no discovery message on the broker"; dump_and_fail; }
if docker compose exec -T wa-mqtt mosquitto_sub -h 127.0.0.1 -t '#' -C 1 -W 5 >/dev/null 2>&1; then
  echo "the broker let an anonymous client in"; dump_and_fail
fi

step "a key saved from the Settings page"
curl -fsS -X PUT -H "Content-Type: application/json" -d '{"value":"ci-key"}' "$API/v1/settings/keys/tfl_app_key" >/dev/null \
  || { echo "could not save a key from the local network"; dump_and_fail; }
curl -fsS "$API/v1/settings/keys" | grep -q '"name":"tfl_app_key","set":true,"source":"settings"' \
  || { echo "the saved key is not in use"; dump_and_fail; }
curl -fsS -X DELETE "$API/v1/settings/keys/tfl_app_key" >/dev/null || dump_and_fail
code="$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Content-Type: application/json" -H "Origin: http://elsewhere.example" \
  -d '{"value":"x"}' "$API/v1/settings/keys/tfl_app_key")"
[ "$code" = "401" ] || { echo "another site could save a key ($code)"; dump_and_fail; }

step "the graph explorer on Neo4j"
for _ in $(seq 1 40); do
  [ "$(docker compose ps --format '{{.Health}}' wa-graph)" = "healthy" ] && break
  sleep 3
done
curl -fsS -X POST "$API/v1/graph/rebuild" | tee "$WORK/rebuild.json" || dump_and_fail
for preset in around-home disruption fuel-along near-places floods buses trips; do
  curl -fsS -X POST -H "Content-Type: application/json" -d "{\"preset\":\"$preset\"}" "$API/v1/graph/query" >/dev/null \
    || { echo "preset $preset failed"; dump_and_fail; }
done
curl -fsS "$API/v1/graph/overview" >/dev/null || { echo "the overview failed"; dump_and_fail; }
curl -fsS "$API/v1/graph/search?q=round" | grep -q '"Round trip"' || { echo "search did not find the test place"; dump_and_fail; }
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Content-Type: application/json" \
  -d '{"cypher":"MATCH (n) DETACH DELETE n"}' "$API/v1/graph/query")"
[ "$code" = "422" ] || { echo "a writing query was not refused ($code)"; dump_and_fail; }

step "everything still answers after the restore"
if ! docker compose exec -T api python -m app.smoke > "$WORK/smoke2.txt" 2>&1; then
  grep -E "^FAIL|checks passed|Error|Traceback" "$WORK/smoke2.txt" | annotate "smoke after restore" 30
  dump_and_fail
fi
echo; echo "stack check passed"; echo "::notice title=stack step::all steps passed"

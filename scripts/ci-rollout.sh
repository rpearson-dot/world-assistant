#!/usr/bin/env bash
# The staged update, for real (CI, or any machine with Docker): the previous release runs behind the front door with
# requests going on all the time, and is updated to this checkout by scripts/deploy.sh. Every request must be
# answered, and the old version must still pass every check on the new database (docs/migrations.md). Then a new
# version that will not start is refused, with the running one answering throughout.
#
# The previous release is built from its git tag, so this needs no registry login.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

WORK="$(mktemp -d)"
export WA_SECRETS_DIR="$WORK/secrets" WA_DATA_DIR="$WORK/data" WA_API_PORT="${WA_API_PORT:-18011}" COMPOSE_PROJECT_NAME=wa-roll
export COMPOSE_FILE=docker-compose.yml
export WA_NH_POLL_ENABLED=false WA_NH_CAPTURE_ENABLED=false WA_FUEL_POLL_ENABLED=false WA_NAPTAN_POLL_ENABLED=false WA_FLOOD_POLL_ENABLED=false WA_CODEPOINT_POLL_ENABLED=false WA_BOUNDARY_POLL_ENABLED=false WA_USRN_POLL_ENABLED=false WA_ENERGY_POLL_ENABLED=false WA_TFLROADS_POLL_ENABLED=false
export WA_PG_TAG=18-3.6 WA_PG_DIR=pg18 WA_PG_MOUNT=/var/lib/postgresql
export WA_CANARY_S=20 WA_KEEP_S=20 WA_WATCH_EVERY_S=5 WA_CHECK_OLD=1 WA_NO_PULL=1 CI="${CI:-1}"
unset WA_REDIS_URL COMPOSE_PROFILES
mkdir -p "$WA_DATA_DIR/backups" "$WA_DATA_DIR/$WA_PG_DIR" "$WA_DATA_DIR/keys" && chmod 777 "$WA_DATA_DIR/backups" "$WA_DATA_DIR/keys"
[ -f .env ] && { echo ".env exists; this script is for a clean checkout (CI)"; exit 2; }
bash scripts/init-secrets.sh >/dev/null
API="http://127.0.0.1:$WA_API_PORT"
TOKEN="$(cat "$WA_SECRETS_DIR/api_token.txt")"

CURRENT=start
fail() {
  echo "::error title=rollout check::$CURRENT: $*"
  docker compose ps -a || true; docker compose logs --tail 40 api wa-proxy || true
  docker logs --tail 40 wa-roll-api-next 2>/dev/null || true
  exit 1
}
cleanup() {
  local s=$?
  [ -n "${POLL:-}" ] && kill "$POLL" 2>/dev/null
  docker rm -f wa-roll-api-next wa-roll-worker-next >/dev/null 2>&1 || true
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  git worktree remove --force "$WORK/prev" >/dev/null 2>&1 || true
  rm -rf "$WORK" 2>/dev/null || true
  exit "$s"
}
trap cleanup EXIT
step() { CURRENT="$*"; printf '\n== %s\n' "$*"; echo "::notice title=rollout step::$*"; }
# The version answering through the front door (the status endpoint has it in every release)
version() { curl -fsS -H "Authorization: Bearer $TOKEN" "$API/v1/status" | sed -n 's/^{"time":"[^"]*","version":"\([^"]*\)".*/\1/p'; }
# Requests all the time, through the front door: a page, an endpoint with the database, and health
poll() {
  while :; do
    for u in /healthz /v1/places /settings; do
      code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$API$u")"
      echo "$(date +%T) $u $code" >> "$WORK/codes"
    done
    sleep 0.2
  done
}
check_codes() {
  local n bad
  n="$(wc -l < "$WORK/codes")"; bad="$(grep -vc ' 200$' "$WORK/codes" || true)"
  echo "$n requests during the update, $bad not answered with 200"
  [ "$n" -gt 50 ] || fail "too few requests were made ($n)"
  [ "$bad" = 0 ] || { grep -v ' 200$' "$WORK/codes" | head -20 || true; fail "$bad requests failed during the update"; }
  : > "$WORK/codes"
}

step "build the previous release and this checkout"
git fetch -q --depth=1 origin '+refs/tags/v*:refs/tags/v*' 2>/dev/null || true
PREV="$(git tag -l 'v*' --sort=-v:refname | head -1)"
[ -n "$PREV" ] || fail "no release tag to start from"
git worktree add -q --detach "$WORK/prev" "$PREV"
docker build -q -t wa-roll:prev --build-arg VERSION="${PREV#v}" "$WORK/prev" >/dev/null
NEXT="$(sed -n 's/^version = "\([^"]*\)".*/\1/p' pyproject.toml | head -1)+ci"
docker build -q -t wa-roll:new --build-arg VERSION="$NEXT" . >/dev/null

step "the previous release ($PREV) behind the front door"
WA_IMAGE=wa-roll:prev docker compose up -d --no-build || fail "the stack did not start"
for _ in $(seq 1 60); do curl -fsS "$API/healthz" >/dev/null 2>&1 && break; sleep 3; done
[ "$(version)" = "${PREV#v}" ] || fail "expected ${PREV#v} to answer, got $(version)"
docker compose exec -T api python -m app.smoke --seed >/dev/null || fail "the previous release failed its own checks"

step "staged update to this checkout, with requests running"
poll & POLL=$!
WA_IMAGE=wa-roll:new bash scripts/deploy.sh --skip-backup || fail "the staged update failed"
kill "$POLL"; POLL=""
check_codes
[ "$(version)" = "$NEXT" ] || fail "the new version is not the one answering"
[ -z "$(docker ps -q --filter name=wa-roll-api-next)" ] || fail "the extra copy of the api was left running"
[ "$(docker inspect -f '{{.Config.Image}}' "$(docker compose ps -q worker)")" = wa-roll:new ] || fail "the worker was not replaced"

step "a new version that will not start is refused, and nothing stops answering"
printf 'FROM wa-roll:new\nCMD ["python", "-c", "raise SystemExit(1)"]\n' | docker build -q -t wa-roll:bad - >/dev/null
poll & POLL=$!
rc=0; WA_IMAGE=wa-roll:bad bash scripts/deploy.sh --skip-backup || rc=$?
kill "$POLL"; POLL=""
[ "$rc" = 3 ] || fail "deploy.sh should say 3 (not applied, old one answered), said $rc"
check_codes
[ "$(version)" = "$NEXT" ] || fail "the working version should still be answering"
grep -q 'to api:8000' <(docker compose exec -T wa-proxy cat /live/upstreams.caddy) || fail "the front door should point at the api service"

step "who connected is still seen through the front door"
code="$(curl -s -o /dev/null -w '%{http_code}' "$API/v1/places")"
[ "$code" = 200 ] || fail "a request from this machine should need no token ($code)"
code="$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Forwarded-For: 203.0.113.9' "$API/v1/places")"
[ "$code" = 401 ] || fail "a request that came through another proxy must need the token ($code)"
code="$(curl -s -o /dev/null -w '%{http_code}' -H 'X-WA-Peer: 127.0.0.1' -H 'X-WA-Hops:' -H 'X-Forwarded-For: 203.0.113.9' "$API/v1/places")"
[ "$code" = 401 ] || fail "the front door's own headers must not be accepted from a client ($code)"
echo; echo "rollout check passed"

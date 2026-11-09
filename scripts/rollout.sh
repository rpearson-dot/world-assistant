#!/usr/bin/env bash
# A staged update with no gap: the new version starts beside the running one, is checked, takes a share of the
# requests, then all of them, while the old one waits to take them back. Run by scripts/deploy.sh when the stack
# is up with its front door (wa-proxy); the new image must already be here (WA_IMAGE, or built).
#
#   1. database changes run first. They only ever add (docs/migrations.md), so the running version carries on.
#   2. the new api starts beside the old one (a one-off copy of the api service: same settings, overlays, secrets),
#      and must become healthy and pass the page and endpoint check before it sees a request.
#   3. trial: one request in five goes to it for WA_CANARY_S seconds (120). Any server error, or it falling ill,
#      sends everything back to the old one.
#   4. all requests go to it, and the old one keeps running for WA_KEEP_S seconds (600), watched the same way: going
#      back is one switch, with nothing restarted.
#   5. the new worker starts beside the old one, the old worker finishes its running jobs and stops, the api
#      service itself is replaced by the new version and takes the requests back, and the extra copies go.
#
# Exit 0: done, the new version runs. 3: the new version failed a check and the old one answered throughout (the
# database keeps the added parts, which the old version ignores). 4: the new version is answering, but the last
# swap did not finish; see the message. 5 (CI only, WA_CHECK_OLD=1): the running version failed on the changed
# database. Anything else: the stack could not be read.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

P="${COMPOSE_PROJECT_NAME:-world-assistant}"
NEXT_API="$P-api-next"
NEXT_WORKER="$P-worker-next"
CANARY_S="${WA_CANARY_S:-120}"
KEEP_S="${WA_KEEP_S:-600}"
WATCH_S="${WA_WATCH_EVERY_S:-10}"

log() { printf '%s  %s\n' "$(date '+%F %T')" "$*"; }
cid() { docker compose ps -q "$1" 2>/dev/null | head -1; }
health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null; }
wait_healthy() { # container seconds
  local end=$((SECONDS + $2)) h
  while [ $SECONDS -lt $end ]; do
    h="$(health "$1")"
    case "$h" in healthy) return 0 ;; exited|dead|"") return 1 ;; esac
    sleep 3
  done
  return 1
}
errors_of() {
  docker exec "$1" python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz/errors', timeout=5))['errors'])" 2>/dev/null
}
# Which api the front door sends requests to: one name, or two with their shares
send_to() {
  local conf
  if [ $# = 1 ]; then conf="to $1:8000"$'\n'"lb_policy first"
  else conf="to $1:8000 $2:8000"$'\n'"lb_policy weighted_round_robin $3 $4"; fi
  printf '# Written by scripts/rollout.sh at %s\n%s\n' "$(date '+%F %T')" "$conf" \
    | docker compose exec -T wa-proxy sh -c 'cat > /live/upstreams.caddy.new && mv /live/upstreams.caddy.new /live/upstreams.caddy' \
    && docker compose exec -T wa-proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
}
drop_next() { docker rm -f "$NEXT_API" "$NEXT_WORKER" >/dev/null 2>&1 || true; }
back() { # why: everything to the old api, the new copies gone
  log "Going back: $1"
  send_to api || log "Could not switch the front door back to the running api!"
  docker logs --tail 30 "$NEXT_API" 2>&1 | sed 's/^/    new api: /' | tail -30
  drop_next
  exit 3
}
# Watch the new api for a while: it must stay healthy and answer no server errors
watch() { # seconds base_errors
  local end=$((SECONDS + $1)) n
  while [ $SECONDS -lt $end ]; do
    sleep "$WATCH_S"
    [ "$(health "$NEXT_API")" = healthy ] || back "the new api stopped being healthy"
    n="$(errors_of "$NEXT_API")"
    [ -n "$n" ] || back "the new api stopped answering"
    [ "$n" -le "$2" ] || back "the new api answered $((n - $2)) server error(s)"
  done
}

[ -n "$(cid wa-proxy)" ] && [ -n "$(cid api)" ] || { log "The stack is not running with its front door; cannot stage."; exit 2; }
drop_next  # anything left from a run that died

log "Staged update 1/5: database changes (they only add, so the running version carries on)."
docker compose run --rm --no-deps migrate || { log "The database changes failed; nothing else was changed."; exit 3; }
if [ "${WA_CHECK_OLD:-}" = 1 ]; then  # CI: the running version must still pass every check on the changed database
  docker compose exec -T api python -m app.smoke || { log "The running version fails on the changed database: a migration removed or changed something it uses."; exit 5; }
fi

log "Staged update 2/5: starting the new version beside the running one."
docker compose run -d --no-deps --name "$NEXT_API" api >/dev/null || back "the new api would not start"
wait_healthy "$NEXT_API" 150 || back "the new api did not become healthy"
docker exec "$NEXT_API" python -m app.smoke || back "the new api failed the page and endpoint check"
base="$(errors_of "$NEXT_API")"
[ -n "$base" ] || back "the new api does not report its errors"
new_ver="$(docker exec "$NEXT_API" python -c 'from app.version import VERSION; print(VERSION)' 2>/dev/null | tr -d '\r')"

log "Staged update 3/5: trial of ${new_ver:-the new version}, one request in five, for ${CANARY_S} s."
send_to api "$NEXT_API" 4 1 || back "the front door could not be switched"
watch "$CANARY_S" "$base"

log "Staged update 4/5: every request to ${new_ver:-the new version}; the old one stands by for ${KEEP_S} s."
send_to "$NEXT_API" || back "the front door could not be switched"
watch "$KEEP_S" "$base"

log "Staged update 5/5: handing over the worker and replacing the api service."
docker compose run -d --no-deps --name "$NEXT_WORKER" worker >/dev/null || back "the new worker would not start"
wait_healthy "$NEXT_WORKER" 150 || back "the new worker did not become healthy"
# From here the old version is going: the new one is answering and working, so problems are fixed forwards
if ! docker compose up -d --no-deps --no-build api worker; then
  log "Could not replace the api and worker services; the new version keeps answering from $NEXT_API."; exit 4
fi
if ! wait_healthy "$(cid api)" 150; then
  log "The replaced api service did not become healthy; requests stay on $NEXT_API, which runs the same version."; exit 4
fi
send_to api || { log "Could not move requests back to the api service; they stay on $NEXT_API."; exit 4; }
sleep 5  # requests already on their way to the copy finish
docker stop -t 30 "$NEXT_API" >/dev/null 2>&1; docker rm -f "$NEXT_API" >/dev/null 2>&1 || true
if ! wait_healthy "$(cid worker)" 150; then
  log "The replaced worker did not become healthy; $NEXT_WORKER keeps doing its jobs."; exit 4
fi
docker stop -t 150 "$NEXT_WORKER" >/dev/null 2>&1; docker rm -f "$NEXT_WORKER" >/dev/null 2>&1 || true
log "Staged update done: ${new_ver:-the new version} is running, and requests were answered throughout."

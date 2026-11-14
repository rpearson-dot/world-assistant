#!/usr/bin/env bash
# The updater container's loop (docker-compose.yml, service wa-updater). Every minute it asks World Assistant
# whether "Update now" was pressed in Settings, and once a night at 04:00 it runs the nightly update, which does
# something only when automatic updates are switched on. Both are scripts/auto-deploy.sh; it takes a lock, so
# only one update runs at a time, and a User Script left over from before waits its turn.
#
# Its log is WA_DATA_DIR/auto-deploy.log, as before. The nightly time is set in Settings (Automatic updates), else
# WA_UPDATE_AT (HH:MM), else 04:00. A nightly run held because the car is charging (auto-deploy.sh exits 75) is
# tried again every 10 minutes for three hours from that time, then left for the next night.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

[ -f .env ] && { set -a; . ./.env; set +a; }
DATA="${WA_DATA_DIR:-/mnt/user/appdata/world-assistant}"
AT="${WA_UPDATE_AT:-04:00}"
LOG="$DATA/auto-deploy.log"
STAMP="$DATA/updater-nightly.day"
mkdir -p "$DATA"
trap 'exit 0' TERM INT
SECRETS="${WA_SECRETS_DIR:-/mnt/user/appdata/world-assistant/secrets}"
API="${AUTO_DEPLOY_API:-http://127.0.0.1:${WA_API_PORT:-8010}}"
echo "$(date '+%F %T')  Updater started: Update now is checked every minute; the nightly update runs at the time set in Settings (else $AT)." >> "$LOG"

# minutes since the nightly time (0..1439), so the window can cross midnight
since_at() {
  local at="$1" h m nh nm
  h=$((10#${at%%:*})) m=$((10#${at##*:})) nh=$((10#$(date +%H))) nm=$((10#$(date +%M)))
  echo $(( ((nh * 60 + nm) - (h * 60 + m) + 1440) % 1440 ))
}

while true; do
  today="$(date +%F)"
  # the time set in Settings, when there is one
  token="$(cat "$SECRETS/api_token.txt" 2>/dev/null || true)"
  set_at="$(curl -fsS --max-time 10 -H "Authorization: Bearer $token" "$API/v1/auto-deploy" 2>/dev/null \
            | sed -n 's/.*"nightly_at":"\([0-9][0-9]*:[0-9][0-9]\)".*/\1/p')"
  at="${set_at:-$AT}"
  mins="$(since_at "$at")"
  # The nightly run: from the nightly time, once a day (a restart at noon does not set it off). Held (exit 75):
  # again every 10 minutes for three hours.
  stamp="$(cat "$STAMP" 2>/dev/null)"
  run=0
  if [ "$mins" -lt 60 ] && [ "${stamp%% *}" != "$today" ]; then run=1; fi
  if [ "$mins" -lt 180 ] && [ "${stamp%% *}" = "$today" ] && [ "$(echo "$stamp" | cut -d' ' -f2)" = held ] \
     && [ $(( $(date +%s) - $(echo "$stamp" | cut -d' ' -f3) )) -ge 600 ]; then run=1; fi
  if [ "$run" = 1 ]; then
    echo "$today" > "$STAMP"
    bash scripts/auto-deploy.sh >> "$LOG" 2>&1
    [ $? -eq 75 ] && echo "$today held $(date +%s)" > "$STAMP"
  fi
  bash scripts/auto-deploy.sh --if-requested >> "$LOG" 2>&1
  # Outside modules: checks, starts, usage and removals World Assistant has queued (scripts/module-runner.py)
  python3 scripts/module-runner.py once >> "$DATA/module-runner.log" 2>&1
  sleep 60 &
  wait $!
done

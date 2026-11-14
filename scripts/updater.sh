#!/usr/bin/env bash
# The updater container's loop (docker-compose.yml, service wa-updater). Every minute it asks World Assistant
# whether "Update now" was pressed in Settings, and once a night at 04:00 it runs the nightly update, which does
# something only when automatic updates are switched on. Both are scripts/auto-deploy.sh; it takes a lock, so
# only one update runs at a time, and a User Script left over from before waits its turn.
#
# Its log is WA_DATA_DIR/auto-deploy.log, as before. WA_UPDATE_AT (HH:MM, default 04:00) moves the nightly run.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

[ -f .env ] && { set -a; . ./.env; set +a; }
DATA="${WA_DATA_DIR:-/mnt/user/appdata/world-assistant}"
AT="${WA_UPDATE_AT:-04:00}"
LOG="$DATA/auto-deploy.log"
STAMP="$DATA/updater-nightly.day"
mkdir -p "$DATA"
trap 'exit 0' TERM INT
echo "$(date '+%F %T')  Updater started: Update now is checked every minute; the nightly update runs at $AT." >> "$LOG"

while true; do
  now="$(date +%H:%M)" today="$(date +%F)"
  # The nightly run: in the hour from WA_UPDATE_AT, once a day (a restart at noon does not set it off)
  if [[ "$now" > "$AT" || "$now" = "$AT" ]] && [[ "$now" < "$(date -d "$AT 1 hour" +%H:%M)" ]] \
     && [ "$(cat "$STAMP" 2>/dev/null)" != "$today" ]; then
    echo "$today" > "$STAMP"
    bash scripts/auto-deploy.sh >> "$LOG" 2>&1
  fi
  bash scripts/auto-deploy.sh --if-requested >> "$LOG" 2>&1
  # Outside modules: checks, starts, usage and removals World Assistant has queued (scripts/module-runner.py)
  python3 scripts/module-runner.py once >> "$DATA/module-runner.log" 2>&1
  sleep 60 &
  wait $!
done

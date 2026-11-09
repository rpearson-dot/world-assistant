#!/usr/bin/env bash
# Deploy or upgrade: back up first, then build or pull, start, and check.
#
#   bash scripts/deploy.sh                 # back up, then build from this folder and start
#   WA_IMAGE=ghcr.io/...@sha256:... bash scripts/deploy.sh   # same, using a released image
#   bash scripts/deploy.sh --skip-backup   # first install, or you have just taken one
#   bash scripts/deploy.sh --classic       # replace everything at once (a short gap), even when a staged update could run
#   bash scripts/deploy.sh --infra         # bring Postgres, Redis, the front door and the rest to their newest images
#
# When the stack is already running with its front door (wa-proxy), World Assistant itself is updated in stages with
# no gap (scripts/rollout.sh): the new version starts beside the old one, is checked, tried, then takes over. The
# other containers (Postgres, Redis, Tailscale, ntfy, the front door) are left alone then; --infra updates those,
# which restarts the ones with a newer image (the nightly update does it at 04:00). The first deploy with the front
# door, or --classic, replaces everything at once as before.
#
# Exit 3: a staged update was not applied because the new version failed a check, and the old one kept answering.
#
# With WA_IMAGE set (the nightly update pins the release it installed) on a git checkout, the pin is kept on the
# same release as the files: if they differ, the files' release image is used (signature checked), or the deploy
# stops before anything changes when there is no release for these files (scripts/lib-release.sh).
#
# The backup is taken by the version that is running now, before anything changes. If it fails the
# deploy stops, so you never upgrade without a copy of your data.
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_BACKUP=0 CLASSIC=0 INFRA=0
for arg in "$@"; do
  case "$arg" in --skip-backup) SKIP_BACKUP=1 ;; --classic) CLASSIC=1 ;; --infra) INFRA=1 ;; esac
done

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
DATA="${WA_DATA_DIR:-/mnt/user/appdata/world-assistant}"
BACKUPS="$DATA/backups"

# shellcheck source=scripts/lib-release.sh
[ -f scripts/lib-release.sh ] && . scripts/lib-release.sh
if declare -F wa_align_image >/dev/null && ! wa_align_image; then
  echo "Nothing was changed."
  exit 1
fi

# The app runs as uid 10001 and must be able to write here.
mkdir -p "$BACKUPS"
chown 10001:10001 "$BACKUPS" 2>/dev/null || chmod 777 "$BACKUPS"
chmod 770 "$BACKUPS" 2>/dev/null || true
# Keys saved from the Settings page: the app writes here too.
mkdir -p "$DATA/keys"
chown 10001:10001 "$DATA/keys" 2>/dev/null || chmod 777 "$DATA/keys"
chmod 700 "$DATA/keys" 2>/dev/null || true
# The supermarket service (docker-compose.supermarket.yml) runs as uid 10001 too and keeps retailer logins here.
case ":${COMPOSE_FILE:-}:" in
  *:docker-compose.supermarket.yml:*)
    mkdir -p "$DATA/supermarket"
    chown 10001:10001 "$DATA/supermarket" 2>/dev/null || chmod 777 "$DATA/supermarket"
    chmod 700 "$DATA/supermarket" 2>/dev/null || true ;;
esac

running() { [ -n "$(docker compose ps --status running -q "${1:-api}" 2>/dev/null)" ]; }
# Every service but the updater: the updater runs this script, and must not be stopped part-way by it
others() { docker compose config --services 2>/dev/null | grep -vx wa-updater; }

# The updater container (docker-compose.yml, wa-updater) needs the profile on and the project folder's path, in .env.
# WA_UPDATER=off in .env leaves it out. Not done in CI, or when there is no .env (a bare checkout).
if [ -f .env ] && [ -z "${CI:-}" ] && [ "${WA_UPDATER:-on}" != "off" ]; then
  here="$(pwd)"
  grep -q '^WA_PROJECT_DIR=' .env || { printf 'WA_PROJECT_DIR=%s\n' "$here" >> .env; export WA_PROJECT_DIR="$here"; }
  if ! grep -q '^COMPOSE_PROFILES=' .env; then
    printf 'COMPOSE_PROFILES=updater\n' >> .env; export COMPOSE_PROFILES=updater
  fi
fi

if [ "$INFRA" = 1 ]; then
  # Only the images that have a newer version are restarted: compose says which it would recreate
  docker compose pull --ignore-buildable --quiet 2>/dev/null || echo "Could not pull every image; using what is here."
  mapfile -t svcs < <(others)
  plan="$(docker compose up -d --no-build --dry-run "${svcs[@]}" 2>&1 || true)"
  if ! printf '%s' "$plan" | grep -qiE 'recreate|create'; then
    echo "Every container already runs its newest image."; exit 0
  fi
  printf '%s\n' "$plan" | grep -iE 'recreate|create' | sed 's/^/  /'
  if [ "$SKIP_BACKUP" = 0 ] && running; then docker compose exec -T api python -m app.backup create || { echo "The backup failed; nothing changed."; exit 1; }; fi
  front_before="$(docker compose ps -q wa-proxy 2>/dev/null)"
  docker compose up -d --no-build "${svcs[@]}"
  if [ -n "$(docker compose ps --status running -q wa-tailscale 2>/dev/null)" ] && [ "$front_before" != "$(docker compose ps -q wa-proxy 2>/dev/null)" ]; then
    docker compose restart wa-tailscale >/dev/null 2>&1 || true  # it holds on to the old front door's address
  fi
  for _ in $(seq 1 40); do
    curl -fsS "http://127.0.0.1:${WA_API_PORT:-8010}/healthz" >/dev/null 2>&1 && { echo "Updated; World Assistant answers."; exit 0; }
    docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" >/dev/null 2>&1 \
      && { echo "Updated; World Assistant answers."; exit 0; }
    sleep 3
  done
  echo "World Assistant did not answer after the update. Look at: docker compose ps"; exit 1
fi

if [ "$SKIP_BACKUP" = 1 ]; then
  echo "Skipping the backup (--skip-backup)."
elif running; then
  echo "Backing up before the upgrade..."
  if ! docker compose exec -T api python -c "import app.backup" >/dev/null 2>&1; then
    # The running version is older than the built-in backup: use a database dump this once.
    f="$BACKUPS/wa-$(date +%Y%m%d-%H%M%S)-pgdump.dump"
    echo "The running version has no built-in backup; taking a full database dump instead."
    if docker compose exec -T wa-db pg_dump -U worldassistant -Fc worldassistant > "$f"; then
      echo "wrote $f"
    else
      rm -f "$f"
      echo "The database dump failed, so nothing has been changed."
      exit 1
    fi
  elif ! docker compose exec -T api python -m app.backup create; then
    echo
    echo "The backup failed, so nothing has been changed. Fix that first (see docs/backup.md),"
    echo "or run again with --skip-backup if you accept the risk."
    exit 1
  fi
else
  echo "The api is not running, so there is nothing to back up yet."
fi

# A release can add a secret file that compose mounts. Create any that are missing (empty; existing files
# are never touched) so the start cannot fail with "bind source path does not exist".
bash scripts/init-secrets.sh >/dev/null || echo "Could not check the secret files; if the start fails on a missing file, run: bash scripts/init-secrets.sh"

STAGED=0
[ "$CLASSIC" = 0 ] && running && running wa-proxy && STAGED=1

if [ "$STAGED" = 1 ]; then
  # Only World Assistant's own image changes; the rest waits for --infra
  if [ -n "${WA_IMAGE:-}" ]; then
    [ -n "${WA_NO_PULL:-}" ] || docker compose pull api
  else
    docker compose build --pull api
  fi
  rc=0; bash scripts/rollout.sh || rc=$?
  if [ "$rc" = 3 ]; then
    echo "The new version was not put in place: it failed a check, and the running version answered throughout."
    exit 3
  elif [ "$rc" != 0 ]; then
    echo "The staged update did not finish (above)."; exit "$rc"
  fi
else
  mapfile -t svcs < <(others)
  if [ -n "${WA_IMAGE:-}" ]; then
    [ -n "${WA_NO_PULL:-}" ] || docker compose pull --ignore-buildable
    docker compose build wa-updater >/dev/null 2>&1 || true
    docker compose up -d --no-build "${svcs[@]}"
  else
    # Fetch the latest Redis, Postgres, OpenBao and so on (same tags, newest patch), and the newest
    # Python base image, so security fixes arrive with every deploy.
    docker compose pull --ignore-buildable || echo "Could not pull the latest third-party images; using what is here."
    docker compose build --pull
    docker compose up -d "${svcs[@]}"
  fi
fi
# The updater: started (or brought up to date) from here, but never by itself mid-run; scripts/auto-deploy.sh
# replaces it last, from a separate container
if [ "${WA_IN_UPDATER:-}" != 1 ] && others >/dev/null && docker compose config --services 2>/dev/null | grep -qx wa-updater; then
  docker compose up -d --no-deps --build wa-updater >/dev/null 2>&1 || echo "Could not start the updater: docker compose up -d wa-updater"
fi

# If the bundled OpenBao is part of this stack, unseal it when a key is stored on this server.
if [ -x scripts/openbao.sh ] && [ -n "$(docker compose ps --status running -q openbao 2>/dev/null)" ]; then
  bash scripts/openbao.sh unseal --quiet --if-sealed || true
fi

echo "Waiting for the api to become healthy..."
for _ in $(seq 1 40); do
  if docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" \
    >/dev/null 2>&1; then
    echo "The api is up."
    # The release running must be the one the files are: a stale pin would otherwise go unnoticed.
    running="$(docker compose exec -T api python -c 'from app.version import VERSION; print(VERSION)' 2>/dev/null | tr -d '\r')"
    files="$(sed -n 's/^version = "\([^"]*\)".*/\1/p' pyproject.toml 2>/dev/null | head -1)"
    echo "Running World Assistant ${running:-unknown} (these files are ${files:-unknown})."
    if [ -n "${WA_IMAGE:-}" ] && [ -n "$running" ] && [ -n "$files" ] && [ "${running%%+*}" != "$files" ]; then
      echo "WARNING: the running release is $running but these files are $files. The pinned image (WA_IMAGE in .env)"
      echo "was not moved on. Look at the messages above, or run: bash scripts/sync.sh  then this again."
      exit 1
    fi
    docker compose exec -T api python -m app.secretsctl check || true
    # The tailnet's https address reaches the api through the Tailscale container, which held on to the old address
    # when what it points at was replaced. A staged update replaces only the api behind the front door, so there is
    # nothing to restart then.
    if [ "$STAGED" = 0 ] && [ -n "$(docker compose ps --status running -q wa-tailscale 2>/dev/null)" ]; then
      echo "Restarting the Tailscale container, so the tailnet address reaches the new api..."
      docker compose restart wa-tailscale >/dev/null 2>&1 || echo "Could not restart wa-tailscale: run  docker compose restart wa-tailscale"
    fi
    # Read-only comfort check: every page and endpoint answers, nothing returns a 500.
    echo "Checking every page and endpoint..."
    if [ "$STAGED" = 1 ] || docker compose exec -T api python -m app.smoke; then  # a staged update checked it before it went live
      # Every outside source, asked live, in the background: a record to read, never a reason to roll back.
      if [ "${WA_LIVE_CHECK:-1}" != "0" ] && [ -f scripts/live-check.sh ]; then
        mkdir -p "$DATA/live-checks" 2>/dev/null || true
        nohup bash scripts/live-check.sh --out "$DATA/live-checks" >"$DATA/live-checks/last-run.log" 2>&1 &
        echo "Live check started in the background: $DATA/live-checks/latest.txt in a few minutes."
      fi
      exit 0
    fi
    echo "Some checks failed (above). The stack is running; look at: docker compose logs --tail 60 api"
    exit 1
  fi
  sleep 3
done
echo "The api did not become healthy in two minutes. Look at: docker compose logs --tail 40 api"
exit 1

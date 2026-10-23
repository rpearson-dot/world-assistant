#!/usr/bin/env bash
# Deploy or upgrade: back up first, then build or pull, start, and check.
#
#   bash scripts/deploy.sh                 # back up, then build from this folder and start
#   WA_IMAGE=ghcr.io/...@sha256:... bash scripts/deploy.sh   # same, using a released image
#   bash scripts/deploy.sh --skip-backup   # first install, or you have just taken one
#
# The backup is taken by the version that is running now, before anything changes. If it fails the
# deploy stops, so you never upgrade without a copy of your data.
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_BACKUP=0
for arg in "$@"; do [ "$arg" = "--skip-backup" ] && SKIP_BACKUP=1; done

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
DATA="${WA_DATA_DIR:-/mnt/user/appdata/world-assistant}"
BACKUPS="$DATA/backups"

# The app runs as uid 10001 and must be able to write here.
mkdir -p "$BACKUPS"
chown 10001:10001 "$BACKUPS" 2>/dev/null || chmod 777 "$BACKUPS"
chmod 770 "$BACKUPS" 2>/dev/null || true
# Keys saved from the Settings page: the app writes here too.
mkdir -p "$DATA/keys"
chown 10001:10001 "$DATA/keys" 2>/dev/null || chmod 777 "$DATA/keys"
chmod 700 "$DATA/keys" 2>/dev/null || true

running() { [ -n "$(docker compose ps --status running -q api 2>/dev/null)" ]; }

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

if [ -n "${WA_IMAGE:-}" ]; then
  docker compose pull
  docker compose up -d --no-build
else
  # Fetch the latest Redis, Postgres, OpenBao and so on (same tags, newest patch), and the newest
  # Python base image, so security fixes arrive with every deploy.
  docker compose pull --ignore-buildable || echo "Could not pull the latest third-party images; using what is here."
  docker compose build --pull
  docker compose up -d
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
    docker compose exec -T api python -m app.secretsctl check || true
    # Read-only comfort check: every page and endpoint answers, nothing returns a 500.
    echo "Checking every page and endpoint..."
    if docker compose exec -T api python -m app.smoke; then
      exit 0
    fi
    echo "Some checks failed (above). The stack is running; look at: docker compose logs --tail 60 api"
    exit 1
  fi
  sleep 3
done
echo "The api did not become healthy in two minutes. Look at: docker compose logs --tail 40 api"
exit 1

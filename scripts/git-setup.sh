#!/usr/bin/env bash
# One time: turn this project folder into a git checkout of the repository, so updates come from
# GitHub (scripts/sync.sh, and the nightly automatic update) instead of copied files.
#
#   bash scripts/git-setup.sh
#
# While the repository is private it needs a read-only GitHub token saved in github_token.txt in the
# secrets folder (docs/runbook.md says how); a public repository needs none. Your .env, secrets and data are not touched. A tar copy of this folder is kept first.
# Run it again any time: it just re-syncs to the newest main.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/lib-git.sh
. scripts/lib-git.sh

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
  # lib-git.sh was read before .env: pick up a WA_SECRETS_DIR set there
  WA_SECRETS="${WA_SECRETS_DIR:-$WA_SECRETS}"
  WA_GITHUB_TOKEN_FILE="${WA_GITHUB_TOKEN_FILE:-$WA_SECRETS/github_token.txt}"
fi
DATA="${WA_DATA_DIR:-/mnt/user/appdata/world-assistant}"

if [ ! -d .git ]; then
  mkdir -p "$DATA"
  copy="$DATA/project-before-git-$(date +%Y%m%d-%H%M%S).tar.gz"
  tar -czf "$copy" --exclude='./.git' . && echo "Kept a copy of this folder: $copy"
  wa_git init -q -b main
fi
if wa_git remote get-url origin >/dev/null 2>&1; then
  wa_git remote set-url origin "$WA_REPO_URL"
else
  wa_git remote add origin "$WA_REPO_URL"
fi
if ! wa_git fetch -q --tags --prune origin; then
  echo "Could not fetch from $WA_REPO_URL."
  if [ ! -s "$WA_GITHUB_TOKEN_FILE" ]; then
    echo "If the repository is private, create a read-only GitHub token and save it in"
    echo "$WA_GITHUB_TOKEN_FILE first (docs/runbook.md, 'Updating the server from GitHub')."
  else
    echo "Check that the token in $WA_GITHUB_TOKEN_FILE has read access to Contents on this repository, and the network."
  fi
  exit 1
fi
wa_git checkout -q -f -B main origin/main
wa_git config user.name "world-assistant server" && wa_git config user.email "server@localhost"
echo "This folder is now a checkout of $(wa_git rev-parse --short HEAD) on main."
echo "Files here that are not in the repository (.env and so on) were left alone:"
wa_git status --short | sed 's/^/  /' || true
echo "Next: bash scripts/sync.sh --deploy   (any time you want the newest version)"

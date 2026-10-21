#!/usr/bin/env bash
# Bring this project folder (a git checkout; see git-setup.sh) to a version from GitHub.
#
#   bash scripts/sync.sh                 # the newest main
#   bash scripts/sync.sh v0.6.0          # a release (tag)
#   bash scripts/sync.sh <commit>        # an exact commit
#   bash scripts/sync.sh --deploy        # then run scripts/deploy.sh
#
# Files you have changed here that the repository also has are replaced: do not edit tracked files on
# the server. .env, secrets and data are not in the repository and are never touched.
set -uo pipefail
cd "$(dirname "$0")/.."

# The whole script is one function that is read before anything runs: syncing replaces this very
# file, and bash must not be half-way through reading the old one.
sync_main() {
  # shellcheck source=scripts/lib-git.sh
  . scripts/lib-git.sh
  if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
    WA_SECRETS="${WA_SECRETS_DIR:-$WA_SECRETS}"
    WA_GITHUB_TOKEN_FILE="${WA_GITHUB_TOKEN_FILE:-$WA_SECRETS/github_token.txt}"
  fi

  local ref="" deploy=0 arg
  for arg in "$@"; do
    case "$arg" in
      --deploy) deploy=1 ;;
      *) ref="$arg" ;;
    esac
  done
  [ -d .git ] || { echo "This folder is not a git checkout yet: run bash scripts/git-setup.sh first."; return 1; }

  wa_git fetch -q --tags --prune origin || { echo "Could not fetch from GitHub (token or network)."; return 1; }
  local target before after
  if [ -z "$ref" ]; then
    target="origin/main"
  elif wa_git rev-parse -q --verify "refs/tags/$ref^{commit}" >/dev/null; then
    target="refs/tags/$ref"
  elif wa_git rev-parse -q --verify "$ref^{commit}" >/dev/null; then
    target="$ref"
  else
    echo "No such release, tag or commit: $ref"
    return 1
  fi
  before="$(wa_git rev-parse --short HEAD 2>/dev/null || echo none)"
  if [ -z "$ref" ]; then
    wa_git checkout -q -f -B main origin/main || return 1
  else
    wa_git checkout -q -f --detach "$target" || return 1
  fi
  after="$(wa_git rev-parse --short HEAD)"
  if [ "$before" = "$after" ]; then
    echo "Already at $after."
  else
    echo "Updated $before -> $after (${ref:-main})."
    wa_git diff --stat --no-color "$before" "$after" 2>/dev/null | tail -1
  fi
  if [ "$deploy" = 1 ]; then
    exec bash scripts/deploy.sh
  fi
  return 0
}

sync_main "$@"; exit $?

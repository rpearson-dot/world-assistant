#!/usr/bin/env bash
# Nightly automatic update of this server, controlled by the switch on the /status page.
#
#   bash scripts/auto-deploy.sh             # what the nightly schedule runs
#   bash scripts/auto-deploy.sh --dry-run   # say what it would do; change nothing
#
# When the switch is on it: finds the newest release image, verifies its signature, refuses if the
# release needs newer compose files than this folder has (unless the folder is a git checkout, in which
# case it moves the folder to the release tag), takes a backup, updates, runs the page-and-endpoint
# check, and puts the previous version (and project files) back if that check fails. It reports the
# outcome to the status page. Setup: docs/runbook.md ("Automatic updates on this server").
set -uo pipefail
cd "$(dirname "$0")/.."

# The whole script is one function, read in full before anything runs: this job replaces files in the
# project folder (including this one) part-way through, and bash must not be reading the old file then.
auto_deploy_main() {
  DRY_RUN=0
  [ "${1:-}" = "--dry-run" ] && DRY_RUN=1

  if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
  fi
  # shellcheck source=scripts/lib-git.sh
  [ -f scripts/lib-git.sh ] && . scripts/lib-git.sh
  DATA="${WA_DATA_DIR:-/mnt/user/appdata/world-assistant}"
  SECRETS="${WA_SECRETS_DIR:-/mnt/user/appdata/world-assistant/secrets}"
  API="${AUTO_DEPLOY_API:-http://127.0.0.1:${WA_API_PORT:-8010}}"
  REPO_IMAGE="${AUTO_DEPLOY_IMAGE:-ghcr.io/rpearson-dot/world-assistant}"
  REPO_SLUG="${AUTO_DEPLOY_REPO:-rpearson-dot/world-assistant}"
  COSIGN_IMAGE="${AUTO_DEPLOY_COSIGN_IMAGE:-ghcr.io/sigstore/cosign/cosign:v3.0.6}"
  VERIFY="${AUTO_DEPLOY_VERIFY:-true}"

  log() { printf '%s  %s\n' "$(date '+%F %T')" "$*"; }

  # One run at a time.
  mkdir -p "$DATA" 2>/dev/null || true
  exec 9>"$DATA/auto-deploy.lock"
  flock -n 9 || { log "Another update is running; stopping."; exit 0; }

  TOKEN="$(cat "$SECRETS/api_token.txt" 2>/dev/null || true)"
  [ -n "$TOKEN" ] || { log "No api_token.txt in $SECRETS; cannot talk to the api."; exit 1; }
  call() { curl -fsS --max-time 20 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" "$@"; }

  report() { # result message [from] [to]
    local msg
    msg="$(printf '%s' "$2" | tr -d '"\\' | tr '\n\r' '  ' | cut -c1-900)"
    [ "$DRY_RUN" = 1 ] && return 0
    call -X POST "$API/v1/auto-deploy/report" \
      -d "{\"result\":\"$1\",\"message\":\"$msg\",\"from_version\":\"${3:-}\",\"to_version\":\"${4:-}\"}" >/dev/null 2>&1 || true
  }
  set_image() { # ref, or empty to go back to building from this folder
    touch .env
    sed -i '/^WA_IMAGE=/d' .env
    if [ -n "$1" ]; then printf 'WA_IMAGE=%s\n' "$1" >> .env; export WA_IMAGE="$1"; else unset WA_IMAGE; fi
    return 0
  }

  state="$(call "$API/v1/auto-deploy")" || { log "The api did not answer at $API."; exit 1; }
  if ! printf '%s' "$state" | grep -q '"enabled":true'; then
    log "Automatic updates are switched off (status page); nothing to do."
    exit 0
  fi
  running="$(printf '%s' "$state" | sed -n 's/.*"running":{"version":"\([^"]*\)".*/\1/p')"
  prev_ref="${WA_IMAGE:-}"
  log "Running version: ${running:-unknown}. Looking for a newer release..."

  if ! docker pull "$REPO_IMAGE:latest" >/dev/null 2>&1; then
    report failed "could not pull the newest release; is the server logged in to ghcr.io? (see the runbook)" "$running"
    log "Could not pull $REPO_IMAGE:latest."; exit 1
  fi
  new_ref="$(docker image inspect "$REPO_IMAGE:latest" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
  new_ver="$(docker image inspect "$REPO_IMAGE:latest" --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null || true)"
  if [ -z "$new_ref" ] || [ -z "$new_ver" ]; then
    report failed "could not read the digest or version of the newest release" "$running"
    log "Could not read the pulled image."; exit 1
  fi

  semver='^[0-9]+\.[0-9]+\.[0-9]+$'
  newest_of() { printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1; }
  if [ "$new_ref" = "$prev_ref" ] || {
    [[ "$running" =~ $semver ]] && [[ "$new_ver" =~ $semver ]] && [ "$(newest_of "$running" "$new_ver")" = "$running" ]; }; then
    report unchanged "already on the newest release (${running})" "$running" "$running"
    log "Already up to date ($running)."; exit 0
  fi
  log "Newer release found: $new_ver ($new_ref)"

  if [ "$VERIFY" = "true" ]; then
    log "Verifying the signature..."
    identity="(?i)https://github.com/${REPO_SLUG}/.github/workflows/release.yml@.*"
    issuer="https://token.actions.githubusercontent.com"
    if command -v cosign >/dev/null 2>&1; then
      cosign verify "$new_ref" --certificate-identity-regexp "$identity" --certificate-oidc-issuer "$issuer" >/dev/null 2>&1
    else
      docker run --rm -v "${DOCKER_CONFIG:-$HOME/.docker}:/root/.docker:ro" "$COSIGN_IMAGE" verify "$new_ref" \
        --certificate-identity-regexp "$identity" --certificate-oidc-issuer "$issuer" >/dev/null 2>&1
    fi
    if [ $? -ne 0 ]; then
      report refused "the signature of $new_ver could not be verified, so it was not installed" "$running" "$new_ver"
      log "Signature check failed; not installing."; exit 0
    fi
  fi

  checkout=0
  [ -d .git ] && checkout=1
  if [ "$checkout" = 0 ]; then
    new_rev="$(docker run --rm --entrypoint cat "$new_ref" /srv/COMPOSE_REVISION 2>/dev/null | tr -dc '0-9')"
    have_rev="$(tr -dc '0-9' < COMPOSE_REVISION 2>/dev/null || true)"
    if [ "${new_rev:-0}" -gt "${have_rev:-0}" ]; then
      report refused "$new_ver needs newer compose files than this folder has (revision ${new_rev}, have ${have_rev:-0}); update the project folder (scripts/git-setup.sh makes that automatic), then run scripts/deploy.sh once" "$running" "$new_ver"
      log "Release $new_ver needs newer files in this folder; not installing."; exit 0
    fi
  fi

  if [ "$DRY_RUN" = 1 ]; then
    log "Dry run: would back up, $([ "$checkout" = 1 ] && echo "move the project files to v$new_ver, ")then update $running -> $new_ver and run the page and endpoint check."
    exit 0
  fi

  prev_schema="$(docker compose exec -T wa-db psql -U worldassistant -d worldassistant -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]')"
  log "Taking a backup first..."
  backup_out="$(docker compose exec -T api python -m app.backup create 2>&1)" || {
    report failed "the backup failed, so nothing was changed: $(printf '%s' "$backup_out" | tail -1)" "$running" "$new_ver"
    log "Backup failed; nothing changed."; exit 1; }
  backup_name="$(printf '%s\n' "$backup_out" | sed -n 's/^wrote \([^ ]*\) .*/\1/p')"

  prev_head=""
  if [ "$checkout" = 1 ]; then
    prev_head="$(wa_git rev-parse HEAD 2>/dev/null || true)"
    log "Moving the project files to v$new_ver..."
    if ! sync_out="$(bash scripts/sync.sh "v$new_ver" 2>&1)"; then
      report failed "could not move the project files to v$new_ver, so nothing was updated: $(printf '%s' "$sync_out" | tail -1)" "$running" "$new_ver"
      log "Could not sync the project files: $sync_out"; exit 1
    fi
  fi
  log "Updating to $new_ver..."
  set_image "$new_ref"
  if bash scripts/deploy.sh --skip-backup; then
    report updated "now running $new_ver" "$running" "$new_ver"
    log "Updated to $new_ver."; exit 0
  fi

  log "The update failed its checks; going back to ${running:-the previous version}."
  now_schema="$(docker compose exec -T wa-db psql -U worldassistant -d worldassistant -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$prev_schema" ] && [ -n "$now_schema" ] && [ "$prev_schema" != "$now_schema" ]; then
    log "The update changed the database layout ($prev_schema -> $now_schema); undoing that with the new version's own migrations."
    if ! docker compose run --rm --no-deps migrate alembic downgrade "$prev_schema" >/dev/null 2>&1; then
      report failed "$new_ver failed its checks and its database change could not be undone. Restore the backup ${backup_name:-in the backups folder} by hand (docs/backup.md). The new version is still running." "$running" "$new_ver"
      log "Could not undo the database change. Backup: ${backup_name:-unknown}."; exit 1
    fi
  fi
  if [ -n "$prev_head" ] && ! bash scripts/sync.sh "$prev_head" >/dev/null 2>&1; then
    report failed "$new_ver failed its checks and the project files could not be put back. Backup: ${backup_name:-in the backups folder}. Run: bash scripts/sync.sh $prev_head" "$running" "$new_ver"
    log "Could not put the project files back."; exit 1
  fi
  set_image "$prev_ref"
  if bash scripts/deploy.sh --skip-backup; then
    report rolled_back "$new_ver failed its checks; ${running:-the previous version} is running again" "$running" "$new_ver"
    log "Rolled back to ${running:-the previous version}."; exit 1
  fi
  report failed "$new_ver failed its checks and the previous version would not start either. Backup: ${backup_name:-in the backups folder}. See docs/runbook.md" "$running" "$new_ver"
  log "Rollback failed too. Backup: ${backup_name:-unknown}."; exit 1
}

auto_deploy_main "$@"; exit $?

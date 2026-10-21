#!/usr/bin/env bash
# Automatic updates (opt-in). Run by .github/workflows/auto-update.yml, daily and after Dependabot's
# pull requests finish CI. Does nothing unless the repository variable AUTO_UPDATES is "true".
#
#   1. merges open Dependabot pull requests that are safe and have passed CI (policy below)
#   2. notices when the Python base image has changed since the last release
#   3. after a merge, runs CI on main so it stays verified
#   4. only if AUTO_RELEASE is also "true": waits for that CI and releases the next patch version
#      (the release gate still has the last word: tip of main, CI green, new version). Without it, a
#      changed base image opens a GitHub issue instead, and you release when you choose.
#
# Policy (decide): patch updates of Python libraries, Docker base images and compose images, and
# minor updates of Python libraries. Never major updates. Never GitHub Actions updates: the built-in
# token may not change workflow files, so you merge those yourself. Never database images (postgres,
# postgis): their data folder does not carry across versions. Docker and compose images are judged by
# the numbers in their tags (Dependabot gives no update type for them): only a patch-level change passes.
#
# Subcommands for tests: decide (BRANCH, MESSAGE, TITLE), next-version (TAG).
set -euo pipefail

# The numeric series of an image tag: "3.12.9-slim" -> "3.12", "7-alpine" -> "7", "latest" -> "".
series_of() { printf '%s' "$1" | sed -nE 's/^([0-9]+(\.[0-9]+)?).*/\1/p'; }

decide() { # BRANCH, MESSAGE and TITLE in the environment; prints "merge" or "skip: reason"
  local branch="${BRANCH:-}" message="${MESSAGE:-}" title="${TITLE:-}" eco types t pkg from to
  case "$branch" in dependabot/*) ;; *) echo "skip: not a Dependabot branch"; return 0 ;; esac
  eco="$(printf '%s' "$branch" | cut -d/ -f2)"
  case "$eco" in
    pip | docker | docker_compose | docker-compose) ;;
    github_actions) echo "skip: workflow files cannot be changed with the built-in token; merge it yourself"; return 0 ;;
    *) echo "skip: unknown ecosystem '$eco'"; return 0 ;;
  esac
  case "$eco" in
    docker | docker_compose | docker-compose)
      pkg="$(printf '%s' "$title" | sed -n 's/.*[Bb]ump \([^ ]*\) from \([^ ]*\) to \([^ ]*\).*/\1/p')"
      from="$(printf '%s' "$title" | sed -n 's/.*[Bb]ump \([^ ]*\) from \([^ ]*\) to \([^ ]*\).*/\2/p')"
      to="$(printf '%s' "$title" | sed -n 's/.*[Bb]ump \([^ ]*\) from \([^ ]*\) to \([^ ]*\).*/\3/p')"
      [ -n "$pkg" ] && [ -n "$from" ] && [ -n "$to" ] || { echo "skip: cannot read the old and new versions from the title"; return 0; }
      case "$pkg" in
        *postgres* | *postgis* | *mysql* | *mariadb* | *mongo*) echo "skip: $pkg holds your data; upgrade it by hand"; return 0 ;;
      esac
      # Image tags carry no update type, so judge by the numbers: only the patch level may change.
      [ -n "$(series_of "$from")" ] && [ "$(series_of "$from")" = "$(series_of "$to")" ] \
        || { echo "skip: $pkg $from -> $to is more than a patch-level change"; return 0; } ;;
  esac
  types="$(printf '%s\n' "$message" | grep -o 'update-type: version-update:semver-[a-z]*' | sed 's/.*semver-//' | sort -u || true)"
  if [ -z "$types" ] && [ "$eco" = pip ]; then echo "skip: no update type in the commit message"; return 0; fi
  for t in $types; do
    case "$t:$eco" in
      patch:*) ;;
      minor:pip) ;;
      minor:*) echo "skip: a minor $eco update needs a human look"; return 0 ;;
      *) echo "skip: a $t update needs a human look"; return 0 ;;
    esac
  done
  echo merge
}

next_version() { # TAG like v1.2.3 -> 1.2.4
  local v="${TAG#v}"
  printf '%s' "$v" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "bad tag: $TAG" >&2; return 1; }
  printf '%s.%s.%s\n' "$(cut -d. -f1 <<<"$v")" "$(cut -d. -f2 <<<"$v")" "$(( $(cut -d. -f3 <<<"$v") + 1 ))"
}

case "${1:-run}" in
  decide) decide; exit 0 ;;
  next-version) next_version; exit 0 ;;
  run) ;;
  *) echo "usage: $0 [run|decide|next-version]" >&2; exit 2 ;;
esac

: "${REPO:?}"
DRY_RUN="${DRY_RUN:-0}"
BASE_TAG="${BASE_TAG:-python:3.12-slim}"
CI_WAIT_SECONDS="${CI_WAIT_SECONDS:-1500}"
POLL_SECONDS="${POLL_SECONDS:-20}"
REQUIRED=(test secrets image stack)

log() { printf '%s\n' "$*"; }
act() { if [ "$DRY_RUN" = 1 ]; then log "[dry run] would: $*"; return 0; fi; "$@"; }

if [ "${AUTO_UPDATES:-}" != "true" ]; then
  log "Automatic updates are off (set the repository variable AUTO_UPDATES to true to turn them on)."
  exit 0
fi

ci_green() { # SHA -> 0 when test, secrets, image and stack all succeeded on it
  local sha="$1" out name
  out="$(gh api "repos/$REPO/commits/$sha/check-runs?per_page=100" --jq '
    .check_runs | sort_by(.started_at) | group_by(.name) | map(last) | .[]
    | select(.name == "test" or .name == "secrets" or .name == "image" or .name == "stack")
    | "\(.name) \(.status) \(.conclusion // "none")"')" || return 1
  for name in "${REQUIRED[@]}"; do
    printf '%s\n' "$out" | grep -q "^$name completed success$" || return 1
  done
}

merged=0
rebuild=0

# 1. Dependabot pull requests
prs="$(gh api "repos/$REPO/pulls?state=open&per_page=100" \
  --jq '.[] | select(.user.login == "dependabot[bot]") | [.number, .head.sha, .head.ref, .title] | @tsv')"
while IFS=$'\t' read -r number sha branch title; do
  [ -n "${number:-}" ] || continue
  if ! ci_green "$sha"; then log "#$number $branch: CI has not passed yet, leaving it"; continue; fi
  message="$(gh api "repos/$REPO/commits/$sha" --jq .commit.message)"
  verdict="$(BRANCH="$branch" MESSAGE="$message" TITLE="$title" decide)"
  if [ "$verdict" != "merge" ]; then log "#$number $branch: $verdict"; continue; fi
  if [ "$DRY_RUN" = 1 ]; then log "[dry run] would merge #$number $branch"; merged=1; continue; fi
  if gh api -X PUT "repos/$REPO/pulls/$number/merge" -f merge_method=squash -f sha="$sha" >/dev/null 2>&1; then
    log "#$number $branch: merged"; merged=1
  else
    log "#$number $branch: could not be merged (conflict or changed); leaving it"
  fi
done <<<"$prs"

# 2. Has the Python base image changed since the last release?
latest_tag="$(gh api "repos/$REPO/releases/latest" --jq .tag_name)"
notes="$(gh api "repos/$REPO/releases/latest" --jq '.body // ""')"
recorded="$(printf '%s\n' "$notes" | grep -o 'Base image: `[^`]*`' | head -1 | sed 's/Base image: `//; s/`$//' || true)"
if [ -z "$recorded" ]; then
  log "The last release ($latest_tag) did not record its base image; checking starts with the next release."
else
  current_digest="$(docker buildx imagetools inspect "$BASE_TAG" --format '{{.Manifest.Digest}}')"
  current="${BASE_TAG%%:*}@${current_digest}"
  if [ "$current" != "$recorded" ]; then
    log "The base image has changed: $recorded -> $current"
    rebuild=1
  else
    log "The base image is unchanged."
  fi
fi

if [ "$merged" = 0 ] && [ "$rebuild" = 0 ]; then log "Nothing to release."; exit 0; fi

if [ "${AUTO_RELEASE:-}" != "true" ]; then
  # Merges made with the built-in token do not start CI by themselves, so start it.
  if [ "$merged" = 1 ]; then act gh workflow run ci.yml --repo "$REPO" --ref main; fi
  if [ "$rebuild" = 1 ]; then
    title="The Python base image has changed: release when ready"
    open_issue="$(gh api "repos/$REPO/issues?state=open&per_page=100" --jq ".[] | select(.title == \"$title\") | .number" | head -1)"
    if [ -n "$open_issue" ]; then
      log "An issue about the base image is already open (#$open_issue)."
    else
      act gh api -X POST "repos/$REPO/issues" -f title="$title" \
        -f body="A newer $BASE_TAG is available than the one in $latest_tag ($recorded -> $current). Automatic releases are off, so release when you are ready: Actions > Release > Run workflow. Then deploy with scripts/deploy.sh."
    fi
  fi
  [ "$merged" = 1 ] && log "Merged; automatic releases are off, so release when you are ready."
  exit 0
fi

# 3. CI on the new tip of main, then the next patch release
tip="$(gh api "repos/$REPO/git/ref/heads/main" --jq .object.sha)"
if [ "$merged" = 1 ]; then
  # Merges made with the built-in token do not start CI by themselves, so start it.
  act gh workflow run ci.yml --repo "$REPO" --ref main
  if [ "$DRY_RUN" != 1 ]; then
    deadline=$((SECONDS + CI_WAIT_SECONDS))
    until ci_green "$tip"; do
      [ "$SECONDS" -lt "$deadline" ] || { log "CI did not pass on ${tip:0:7} in time; not releasing."; exit 1; }
      sleep "$POLL_SECONDS"
      tip_now="$(gh api "repos/$REPO/git/ref/heads/main" --jq .object.sha)"
      [ "$tip_now" = "$tip" ] || { log "main moved while waiting; the next run will pick it up."; exit 0; }
    done
  fi
fi
version="$(TAG="$latest_tag" next_version)"
log "Releasing $version"
act gh workflow run release.yml --repo "$REPO" --ref main -f "version=$version"

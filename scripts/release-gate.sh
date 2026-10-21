#!/usr/bin/env bash
# Release gate: refuse to release unless CI has passed on this exact commit. Used by the Release
# workflow; there is deliberately no override. Needs the gh CLI and a token that can read checks.
#
# Inputs (environment): REPO (owner/name), SHA (commit being released), VERSION (X.Y.Z, no "v"),
# EVENT ("dispatch" or "tag"). Optional: WAIT_SECONDS (default 300), POLL_SECONDS (default 15).
#
# Rules, all of which must hold:
#   1. the commit is the current tip of main
#   2. the release v$VERSION does not exist yet (and, for a manual run, neither does the tag)
#   3. the CI checks "test", "secrets", "image" and "stack" have all succeeded on the commit; if they are
#      still running or have not started, wait up to WAIT_SECONDS; if any has failed, stop at once
set -euo pipefail

: "${REPO:?}" "${SHA:?}" "${VERSION:?}" "${EVENT:?}"
WAIT_SECONDS="${WAIT_SECONDS:-300}"
POLL_SECONDS="${POLL_SECONDS:-15}"
REQUIRED=(test secrets image stack)

fail() { printf 'RELEASE REFUSED: %s\n' "$*" >&2; exit 1; }

tip="$(gh api "repos/$REPO/git/ref/heads/main" --jq .object.sha)" || fail "could not read main"
[ "$tip" = "$SHA" ] || fail "commit ${SHA:0:7} is not the tip of main (${tip:0:7}); release from the latest main"

if [ "$EVENT" = "dispatch" ] && gh api "repos/$REPO/git/ref/tags/v$VERSION" >/dev/null 2>&1; then
  fail "the tag v$VERSION already exists"
fi
if gh api "repos/$REPO/releases/tags/v$VERSION" >/dev/null 2>&1; then
  fail "the release v$VERSION already exists"
fi

# Newest run of each required check on this commit: "name status conclusion"
latest() {
  gh api "repos/$REPO/commits/$SHA/check-runs?per_page=100" --jq '
    .check_runs | sort_by(.started_at) | group_by(.name) | map(last) | .[]
    | select(.name == "test" or .name == "secrets" or .name == "image" or .name == "stack")
    | "\(.name) \(.status) \(.conclusion // "none")"'
}

deadline=$((SECONDS + WAIT_SECONDS))
while :; do
  out="$(latest)" || fail "could not read the CI checks"
  pending=()
  for name in "${REQUIRED[@]}"; do
    line="$(printf '%s\n' "$out" | grep "^$name " || true)"
    if [ -z "$line" ]; then pending+=("$name (not started)"); continue; fi
    read -r _ status conclusion <<<"$line"
    if [ "$status" != "completed" ]; then pending+=("$name ($status)"); continue; fi
    [ "$conclusion" = "success" ] || fail "CI check '$name' finished as '$conclusion' on ${SHA:0:7}"
  done
  if [ ${#pending[@]} -eq 0 ]; then
    echo "Release gate passed: ${SHA:0:7} is the tip of main, v$VERSION is new, and CI is green."
    exit 0
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    fail "CI did not finish within ${WAIT_SECONDS}s: ${pending[*]}"
  fi
  echo "Waiting for CI: ${pending[*]}"
  sleep "$POLL_SECONDS"
done

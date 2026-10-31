# shellcheck shell=bash
# Shared by deploy.sh: keep a pinned release image (WA_IMAGE in .env) on the same release as the project files.
#
# The nightly update pins the release it installs (WA_IMAGE=...@sha256:...). Moving the files on by hand
# (sync.sh) used to leave that pin behind, so the deploy started the old image with the new files. Here, when the
# folder is a git checkout and the pinned image's version is not the files' version, the files' release image is
# found, its signature checked, and the pin moved to it; with no release for the files (main ahead of the newest
# release), the deploy stops and says what to do.

wa_files_version() {
  sed -n 's/^version = "\([^"]*\)".*/\1/p' pyproject.toml 2>/dev/null | head -1
}

wa_image_version() { # ref -> its version label, pulling it first
  docker pull -q "$1" >/dev/null 2>&1 || return 1
  docker image inspect "$1" --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null
}

wa_verify_release() { # ref; 0 when signed by this repository's release workflow
  [ "${AUTO_DEPLOY_VERIFY:-true}" = "true" ] || return 0
  local slug="${AUTO_DEPLOY_REPO:-rpearson-dot/world-assistant}"
  local identity="(?i)https://github.com/${slug}/.github/workflows/release.yml@.*"
  local issuer="https://token.actions.githubusercontent.com"
  if command -v cosign >/dev/null 2>&1; then
    cosign verify "$1" --certificate-identity-regexp "$identity" --certificate-oidc-issuer "$issuer" >/dev/null 2>&1
  else
    docker run --rm -v "${DOCKER_CONFIG:-$HOME/.docker}:/root/.docker:ro" \
      "${AUTO_DEPLOY_COSIGN_IMAGE:-ghcr.io/sigstore/cosign/cosign:v3.0.6}" verify "$1" \
      --certificate-identity-regexp "$identity" --certificate-oidc-issuer "$issuer" >/dev/null 2>&1
  fi
}

wa_set_image() { # ref: the one WA_IMAGE line in .env
  touch .env
  sed -i '/^WA_IMAGE=/d' .env
  printf 'WA_IMAGE=%s\n' "$1" >> .env
  export WA_IMAGE="$1"
}

wa_align_image() {
  # Returns 0 when there is nothing to do or the pin was moved; 1 (with the reason printed) when the deploy should stop.
  [ -n "${WA_IMAGE:-}" ] || return 0   # building from the folder: the files are the version
  [ -d .git ] || return 0              # not a checkout: the files do not say which release they are
  local files pinned repo ref
  files="$(wa_files_version)"
  [ -n "$files" ] || return 0
  pinned="$(wa_image_version "$WA_IMAGE")" || {
    echo "Could not pull the pinned image $WA_IMAGE (is the server logged in to ghcr.io?)."; return 1; }
  [ "$pinned" = "$files" ] && return 0
  repo="${WA_IMAGE%%@*}"
  repo="${repo%:*}"
  echo "The pinned image is $pinned but the project files are $files: moving the pin to the $files release."
  if ! docker pull -q "$repo:$files" >/dev/null 2>&1; then
    echo "There is no $files release image ($repo:$files): these files are newer than the newest release."
    echo "Either move the files to a release:   bash scripts/sync.sh v$pinned   (or the newest release)"
    echo "or build from the files instead:      remove the WA_IMAGE line from .env, then run this again."
    return 1
  fi
  ref="$(docker image inspect "$repo:$files" --format '{{index .RepoDigests 0}}' 2>/dev/null)"
  [ -n "$ref" ] || { echo "Could not read the digest of $repo:$files."; return 1; }
  if ! wa_verify_release "$ref"; then
    echo "The signature of the $files image could not be verified, so it was not used. Nothing was changed."
    return 1
  fi
  wa_set_image "$ref"
  echo "WA_IMAGE now pins $files ($ref)."
  return 0
}

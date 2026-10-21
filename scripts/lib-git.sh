# shellcheck shell=bash
# Shared by git-setup.sh and sync.sh: run git against the private repo with a read-only token.
#
# The token is read from github_token.txt in the secrets folder and handed to git through the
# environment, so it never appears in a process list or in .git/config. Uses the git on this server
# if there is one (Unraid does not ship it), otherwise the alpine/git container.

WA_SECRETS="${WA_SECRETS_DIR:-/mnt/user/appdata/world-assistant/secrets}"
WA_GITHUB_TOKEN_FILE="${WA_GITHUB_TOKEN_FILE:-$WA_SECRETS/github_token.txt}"
WA_REPO_URL="${WA_REPO_URL:-https://github.com/rpearson-dot/World-assistant.git}"

wa_git() {
  local token="" header=""
  [ -r "$WA_GITHUB_TOKEN_FILE" ] && token="$(tr -d '[:space:]' < "$WA_GITHUB_TOKEN_FILE")"
  if [ -n "$token" ]; then
    header="Authorization: Basic $(printf 'x-access-token:%s' "$token" | base64 | tr -d '\n')"
  fi
  export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_COUNT=2
  export GIT_CONFIG_KEY_0="http.https://github.com/.extraHeader" GIT_CONFIG_VALUE_0="$header"
  export GIT_CONFIG_KEY_1="safe.directory" GIT_CONFIG_VALUE_1="*"
  if command -v git >/dev/null 2>&1; then
    git "$@"
  else
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e GIT_TERMINAL_PROMPT -e GIT_CONFIG_COUNT \
      -e GIT_CONFIG_KEY_0 -e GIT_CONFIG_VALUE_0 -e GIT_CONFIG_KEY_1 -e GIT_CONFIG_VALUE_1 \
      -v "$PWD:/repo" -w /repo alpine/git "$@"
  fi
}

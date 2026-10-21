#!/usr/bin/env bash
# Set up and look after the bundled OpenBao key vault. See docs/openbao.md.
#
#   bash scripts/openbao.sh setup      # first time: start, initialise, unseal, configure, move secrets in
#   bash scripts/openbao.sh unseal     # after the server or the container restarts
#   bash scripts/openbao.sh import     # copy the secret files into the vault again (after editing them)
#   bash scripts/openbao.sh status
#   bash scripts/openbao.sh store-key  # optional: keep ONE unseal key on this server for automatic unsealing
#   bash scripts/openbao.sh forget-key
#
# The unseal keys and root token are shown ONCE by `setup` and never written to disk. Save them
# somewhere other than this server.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
DATA="${WA_DATA_DIR:-/mnt/user/appdata/world-assistant}"
SECRETS="${WA_SECRETS_DIR:-/mnt/user/appdata/world-assistant/secrets}"
KEYFILE="$SECRETS/openbao_unseal_key.txt"
ROLE=world-assistant

# Always include the vault and OpenBao compose files, whatever else .env asks for.
want="${COMPOSE_FILE:-docker-compose.yml}"
for f in docker-compose.vault.yml docker-compose.openbao.yml; do
  case ":$want:" in *":$f:"*) ;; *) want="$want:$f" ;; esac
done
export COMPOSE_FILE="$want"

# Compose needs these sign-in files to exist (empty is fine).
mkdir -p "$SECRETS"
# Docker makes a folder if it is asked to mount a file that does not exist yet; undo that.
if [ -d "$KEYFILE" ]; then
  docker compose rm -sf openbao-unseal >/dev/null 2>&1 || true
  rmdir "$KEYFILE" 2>/dev/null || { echo "error: $KEYFILE is a folder and not empty; remove it by hand" >&2; exit 1; }
fi
[ -e "$KEYFILE" ] || { : > "$KEYFILE"; chmod 600 "$KEYFILE"; }
for f in vault_token vault_role_id vault_secret_id; do
  [ -e "$SECRETS/$f.txt" ] || { : > "$SECRETS/$f.txt"; chmod 644 "$SECRETS/$f.txt"; }
done

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Run the bao command line inside the container. The token, when set, is passed by name only so it
# never appears in a process list.
bao() { docker compose exec -T -e BAO_TOKEN openbao bao "$@"; }

state() { # prints "initialized sealed", for example "true false"
  local out
  out="$(docker compose exec -T openbao bao status 2>/dev/null || true)"
  printf '%s %s\n' \
    "$(printf '%s\n' "$out" | awk '$1=="Initialized"{print $2}')" \
    "$(printf '%s\n' "$out" | awk '$1=="Sealed"{print $2}')"
}

write_env() { # add or set KEY=VALUE in .env
  local k="$1" v="$2"
  touch .env
  if grep -q "^$k=" .env; then
    sed -i "s|^$k=.*|$k=$v|" .env
  else
    printf '%s=%s\n' "$k" "$v" >> .env
  fi
}

start_container() {
  write_env COMPOSE_FILE "$COMPOSE_FILE"   # so plain "docker compose" commands see the openbao service too
  mkdir -p "$DATA/openbao/file"
  chown -R "${OPENBAO_UID:-99}:${OPENBAO_GID:-100}" "$DATA/openbao/file" 2>/dev/null \
    || chmod -R 777 "$DATA/openbao/file"
  docker compose rm -sf openbao >/dev/null 2>&1 || true   # clear any crash-looping earlier attempt
  docker compose up -d openbao
  say "Waiting for OpenBao to answer..."
  for _ in $(seq 1 30); do
    if [ -n "$(docker compose exec -T openbao bao status 2>/dev/null || true)" ]; then return 0; fi
    sleep 2
  done
  say "OpenBao did not start. Its log:"
  docker compose logs --tail 30 openbao || true
  say "If the log mentions permission denied on /openbao/file, run:"
  say "  chmod -R 777 $DATA/openbao/file   and then run this command again."
  say "(Plain 'docker compose' commands work now too; setup saved COMPOSE_FILE in .env.)"
  exit 1
}

unseal_with() { # $1 = key
  bao operator unseal "$1" >/dev/null
}

cmd_unseal() {
  local quiet=0 only_if_sealed=0
  for a in "$@"; do
    [ "$a" = "--quiet" ] && quiet=1
    [ "$a" = "--if-sealed" ] && only_if_sealed=1
  done
  docker compose ps --status running -q openbao 2>/dev/null | grep -q . || {
    [ "$quiet" = 1 ] && return 0
    die "OpenBao is not running (docker compose up -d openbao)"
  }
  read -r init sealed <<<"$(state)"
  [ "$init" = "true" ] || { [ "$quiet" = 1 ] && return 0; die "OpenBao is not set up yet: bash scripts/openbao.sh setup"; }
  if [ "$sealed" != "true" ]; then [ "$quiet" = 1 ] || say "OpenBao is already unsealed."; return 0; fi
  if [ -s "$KEYFILE" ]; then
    unseal_with "$(tr -d '[:space:]' < "$KEYFILE")"
  elif [ "$only_if_sealed" = 1 ] && [ ! -t 0 ]; then
    say "OpenBao is sealed. Unseal it with: bash scripts/openbao.sh unseal"
    return 0
  else
    local key
    while :; do
      read -r -s -p "Unseal key (typing is hidden): " key; echo
      [ -n "$key" ] || die "no key entered"
      unseal_with "$key"
      read -r _ sealed <<<"$(state)"
      [ "$sealed" = "true" ] || break
      say "Still sealed: another key is needed."
    done
  fi
  read -r _ sealed <<<"$(state)"
  [ "$sealed" = "false" ] && say "OpenBao is unsealed." || say "OpenBao is still sealed."
}

cmd_status() {
  docker compose ps openbao || true
  read -r init sealed <<<"$(state)"
  say "initialised: ${init:-unknown}   sealed: ${sealed:-unknown}"
  [ -s "$KEYFILE" ] && say "automatic unsealing: ON (a key is stored on this server)" || say "automatic unsealing: off"
}

import_secrets() {
  [ -n "${BAO_TOKEN:-}" ] || { read -r -s -p "OpenBao root token (typing is hidden): " BAO_TOKEN; echo; export BAO_TOKEN; }
  say "Copying your secret files into the vault (values are not shown)..."
  docker compose exec -T api python -m app.secretsctl dump-json | bao kv put secret/world-assistant - >/dev/null \
    || die "could not copy the secrets. Has the api been rebuilt with this version? Run: bash scripts/deploy.sh"
  say "Done. Names now in the vault:"
  bao kv get -format=json secret/world-assistant 2>/dev/null \
    | tr ',' '\n' | grep -o '"[a-z_]*":' | grep -v '"data"\|"metadata"\|"version"\|"created_time"\|"custom_metadata"\|"deletion_time"\|"destroyed"\|"request_id"\|"lease_id"\|"renewable"\|"lease_duration"\|"mount_type"\|"warnings"' | tr -d '":' | sed 's/^/  /' || true
}

cmd_import() { docker compose ps --status running -q openbao | grep -q . || die "OpenBao is not running"; cmd_unseal --quiet; import_secrets; }

cmd_setup() {
  start_container
  read -r init sealed <<<"$(state)"
  local shares=1 threshold=1
  for a in "$@"; do
    case "$a" in --shares=*) shares="${a#*=}" ;; --threshold=*) threshold="${a#*=}" ;; esac
  done
  if [ "$init" != "true" ]; then
    say "Initialising OpenBao with $shares unseal key(s), $threshold needed to unseal."
    local out
    out="$(docker compose exec -T openbao bao operator init -key-shares="$shares" -key-threshold="$threshold")"
    say ""
    say "================================================================================"
    say "$out"
    say "================================================================================"
    say "SAVE THE UNSEAL KEY(S) AND THE ROOT TOKEN ABOVE NOW, somewhere other than this server"
    say "(a password manager). They are shown once and are not stored anywhere."
    say ""
    local keys root n=0
    keys="$(printf '%s\n' "$out" | awk '/^Unseal Key [0-9]+:/{print $NF}')"
    root="$(printf '%s\n' "$out" | awk '/^Initial Root Token:/{print $NF}')"
    [ -n "$root" ] || die "could not read the root token from the init output"
    while IFS= read -r k; do
      n=$((n + 1)); [ "$n" -le "$threshold" ] && unseal_with "$k"
    done <<<"$keys"
    export BAO_TOKEN="$root"
    read -r -p "Press Enter once you have saved them... " _
  else
    cmd_unseal --quiet
    read -r _ sealed <<<"$(state)"
    [ "$sealed" = "false" ] || die "OpenBao is sealed: run bash scripts/openbao.sh unseal, then setup again"
    read -r -s -p "OpenBao root token (typing is hidden): " BAO_TOKEN; echo; export BAO_TOKEN
  fi

  say "Configuring OpenBao..."
  if ! bao secrets list 2>/dev/null | grep -q '^secret/'; then
    bao secrets enable -path=secret -version=2 kv >/dev/null
  fi
  printf 'path "secret/data/%s" {\n  capabilities = ["read"]\n}\n' "$ROLE" | bao policy write "$ROLE" - >/dev/null
  if ! bao auth list 2>/dev/null | grep -q '^approle/'; then bao auth enable approle >/dev/null; fi
  bao write "auth/approle/role/$ROLE" token_policies="$ROLE" token_ttl=1h token_max_ttl=24h \
    secret_id_ttl=0 token_num_uses=0 >/dev/null

  say "Creating the app's sign-in (AppRole) files..."
  printf '%s' "$(bao read -field=role_id "auth/approle/role/$ROLE/role-id")" > "$SECRETS/vault_role_id.txt"
  printf '%s' "$(bao write -f -field=secret_id "auth/approle/role/$ROLE/secret-id")" > "$SECRETS/vault_secret_id.txt"
  chmod 644 "$SECRETS/vault_role_id.txt" "$SECRETS/vault_secret_id.txt"

  import_secrets

  say "Pointing the app at the vault (.env)..."
  write_env COMPOSE_FILE "$COMPOSE_FILE"
  write_env WA_SECRETS_BACKEND vault
  write_env WA_VAULT_ADDR http://openbao:8200
  unset BAO_TOKEN

  say ""
  say "Finished. Now restart the app so it uses the vault:"
  say "  bash scripts/deploy.sh --skip-backup"
  say "Then check:  docker compose exec api python -m app.secretsctl check"
  say "Secrets should say 'from vault'. Your secret files stay as a fallback; delete them only once"
  say "you trust the vault and have saved the keys. After a restart, unseal with:"
  say "  bash scripts/openbao.sh unseal"
}

cmd_store_key() {
  say "This keeps ONE unseal key in $KEYFILE so OpenBao unseals itself after every restart."
  say "Anyone who can read that file and the vault data can open the vault. Only do this if you"
  say "used a single key (the default), and accept the trade-off."
  local key
  read -r -s -p "Unseal key (typing is hidden): " key; echo
  key="$(printf '%s' "$key" | sed 's/^[[:space:]]*Unseal Key [0-9]*:[[:space:]]*//' | tr -d '[:space:]')"
  [ -n "$key" ] || die "no key entered"
  case "$key" in *[!A-Za-z0-9+/=]*) die "that does not look like a single unseal key (only letters, digits, + / =). Paste just the key value." ;; esac
  [ "${#key}" -le 100 ] || die "that is too long to be one unseal key; paste only the key, not the whole output or the root token."
  printf '%s' "$key" > "$KEYFILE"
  chown "${OPENBAO_UID:-99}:${OPENBAO_GID:-100}" "$KEYFILE" 2>/dev/null || chmod 644 "$KEYFILE"
  chmod 600 "$KEYFILE" 2>/dev/null || true
  docker compose up -d --force-recreate openbao-unseal
  say "Stored, and the auto-unseal helper is running. It checks every 10 seconds."
  say "Remove it any time with: bash scripts/openbao.sh forget-key"
}

cmd_forget_key() {
  : > "$KEYFILE"
  docker compose stop openbao-unseal >/dev/null 2>&1 || true
  say "Removed. You will unseal by hand after restarts."
}

case "${1:-}" in
  setup) shift; cmd_setup "$@" ;;
  unseal) shift; cmd_unseal "$@" ;;
  import) cmd_import ;;
  status) cmd_status ;;
  store-key) cmd_store_key ;;
  forget-key) cmd_forget_key ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac

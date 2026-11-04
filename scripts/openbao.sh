#!/usr/bin/env bash
# Set up and look after the bundled OpenBao key vault. See docs/openbao.md.
#
#   bash scripts/openbao.sh setup      # first time: start, initialise, unseal, configure, move secrets in
#   bash scripts/openbao.sh unseal     # after the server or the container restarts
#   bash scripts/openbao.sh import     # copy the secret files into the vault again (after editing them)
#   bash scripts/openbao.sh status
#   bash scripts/openbao.sh store-key  # optional: keep ONE unseal key on this server for automatic unsealing
#   bash scripts/openbao.sh forget-key
#   bash scripts/openbao.sh upgrade    # once: move a vault set up before 0.26.0 to raft storage and OpenBao 2.7
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

NEW_VERSION=2.7.2   # the OpenBao for raft storage; 2.6 (file storage) stopped getting fixes on 1 October 2026
RAFT_CONFIG=openbao-raft.hcl

own_dirs() {  # the data folders, owned by the user OpenBao runs as
  local d
  for d in file raft; do
    mkdir -p "$DATA/openbao/$d"
    chown -R "${OPENBAO_UID:-99}:${OPENBAO_GID:-100}" "$DATA/openbao/$d" 2>/dev/null || chmod -R 777 "$DATA/openbao/$d"
  done
}

start_container() {
  write_env COMPOSE_FILE "$COMPOSE_FILE"   # so plain "docker compose" commands see the openbao service too
  # A new vault starts on raft storage and the current OpenBao; one with data in openbao/file stays as it is
  # until `upgrade` moves it (OpenBao 2.7 cannot read file storage).
  if [ -z "${OPENBAO_CONFIG:-}" ] && [ -z "$(ls -A "$DATA/openbao/file" 2>/dev/null)" ]; then
    write_env OPENBAO_CONFIG "$RAFT_CONFIG"; write_env OPENBAO_VERSION "$NEW_VERSION"
    export OPENBAO_CONFIG="$RAFT_CONFIG" OPENBAO_VERSION="$NEW_VERSION"
  fi
  own_dirs
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

storage_type() { docker compose exec -T openbao bao status 2>/dev/null | awk '$1=="Storage"&&$2=="Type"{print $3}'; }
server_version() { docker compose exec -T openbao bao status 2>/dev/null | awk '$1=="Version"{print $2}'; }

wait_answer() {
  for _ in $(seq 1 30); do
    if [ -n "$(docker compose exec -T openbao bao status 2>/dev/null || true)" ]; then return 0; fi
    sleep 2
  done
  return 1
}

cmd_upgrade() {
  local yes=0 a
  for a in "$@"; do case "$a" in --yes | -y) yes=1 ;; esac; done
  if [ "${OPENBAO_CONFIG:-openbao.hcl}" = "$RAFT_CONFIG" ]; then
    if [ "${OPENBAO_VERSION:-}" = "$NEW_VERSION" ]; then say "Already on OpenBao $NEW_VERSION with raft storage. Nothing to do."; return 0; fi
    write_env OPENBAO_VERSION "$NEW_VERSION"; export OPENBAO_VERSION="$NEW_VERSION"
    docker compose up -d openbao openbao-unseal
    wait_answer || die "OpenBao $NEW_VERSION did not start: docker compose logs --tail 30 openbao"
    cmd_unseal --quiet --if-sealed
    say "OpenBao is now $(server_version)."
    return 0
  fi
  [ -n "$(ls -A "$DATA/openbao/file" 2>/dev/null)" ] \
    || die "there is no vault in $DATA/openbao/file to move. For a new vault: bash scripts/openbao.sh setup"
  [ -z "$(ls -A "$DATA/openbao/raft" 2>/dev/null)" ] \
    || die "$DATA/openbao/raft already has files in it (an earlier attempt?). Move or delete it, then run this again."
  say "This moves the vault to OpenBao $NEW_VERSION. OpenBao 2.7 can no longer read the 'file' storage it uses now, so:"
  say "  - its data is copied to raft storage ($DATA/openbao/raft); $DATA/openbao/file is only read, and kept"
  say "  - the vault is down for a minute and comes back sealed: it unseals by itself if the key is stored here,"
  say "    otherwise this asks for the unseal key. The unseal key and root token do not change."
  say "  - if anything fails, it goes back to the file storage as it was."
  if [ "$yes" != 1 ]; then
    local reply; read -r -p "Go ahead? [y/N] " reply
    case "$reply" in y | Y | yes | YES) ;; *) say "Nothing was changed."; return 1 ;; esac
  fi
  local stamp saved tarball
  stamp="$(date +%Y%m%d-%H%M%S)"; saved=".env.before-openbao27"
  mkdir -p "$DATA/backups"
  tarball="$DATA/backups/openbao-file-$stamp.tar.gz"
  docker compose stop openbao-unseal openbao
  tar -czf "$tarball" -C "$DATA/openbao" file || { docker compose up -d openbao openbao-unseal; die "could not copy the vault folder; nothing was changed"; }
  chmod 600 "$tarball"
  say "Copied the vault folder to $tarball"
  own_dirs
  say "Moving the data to raft storage..."
  if ! docker compose run --rm --no-deps openbao operator migrate -config=/openbao/config/migrate-file-to-raft.hcl; then
    rm -rf "${DATA:?}/openbao/raft"/* 2>/dev/null || true
    docker compose up -d openbao openbao-unseal; cmd_unseal --quiet --if-sealed
    die "the move to raft storage failed; the vault is back on file storage, unchanged"
  fi
  cp .env "$saved"
  back() {
    say "The upgrade did not finish ($1). Going back to file storage; nothing is lost."
    docker compose stop openbao-unseal openbao || true
    cp "$saved" .env; unset OPENBAO_CONFIG OPENBAO_VERSION
    docker compose up -d openbao openbao-unseal || true
    wait_answer && cmd_unseal --quiet --if-sealed
    say "$DATA/openbao/raft is left for a look; delete it before trying again."
    exit 1
  }
  write_env OPENBAO_CONFIG "$RAFT_CONFIG"; write_env OPENBAO_VERSION "$NEW_VERSION"
  export OPENBAO_CONFIG="$RAFT_CONFIG" OPENBAO_VERSION="$NEW_VERSION"
  docker compose pull openbao || say "Could not pull openbao/openbao:$NEW_VERSION; using it if it is already here."
  docker compose up -d openbao openbao-unseal || back "OpenBao $NEW_VERSION would not start"
  wait_answer || back "OpenBao $NEW_VERSION did not answer"
  [ "${WA_OPENBAO_FAIL_AT:-}" = started ] && back "a failure asked for by the test (WA_OPENBAO_FAIL_AT)"
  local init sealed
  read -r init sealed <<<"$(state)"
  [ "$init" = "true" ] || back "the moved vault does not show as set up"
  [ "$(storage_type)" = raft ] || back "OpenBao is not using raft storage"
  cmd_unseal --if-sealed
  read -r _ sealed <<<"$(state)"
  if [ "$sealed" = "false" ] && docker compose ps --status running -q api 2>/dev/null | grep -q .; then
    docker compose exec -T api python -m app.secretsctl check || say "World Assistant could not read the vault yet: run bash scripts/deploy.sh --skip-backup"
  fi
  rm -f "$saved"
  say ""
  say "Done: OpenBao $(server_version) on raft storage$([ "$sealed" = "false" ] && echo ", unsealed" || echo "; unseal it with: bash scripts/openbao.sh unseal")."
  say "  The old folder, $DATA/openbao/file, and the copy $tarball are kept. Delete them when you are happy."
}

case "${1:-}" in
  setup) shift; cmd_setup "$@" ;;
  unseal) shift; cmd_unseal "$@" ;;
  import) cmd_import ;;
  status) cmd_status ;;
  store-key) cmd_store_key ;;
  forget-key) cmd_forget_key ;;
  upgrade) shift; cmd_upgrade "$@" ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac

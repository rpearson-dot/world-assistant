#!/usr/bin/env bash
# CI: moving the bundled OpenBao from file storage (2.6) to raft storage (2.7), as `openbao.sh upgrade` does on
# the NAS: a vault with a secret in it on 2.6, a failed upgrade that goes back by itself, then the upgrade.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && { echo ".env exists; this script is for a clean checkout (CI)"; exit 2; }
WORK="$(mktemp -d)"
DATA="$WORK/data"
cat > .env <<ENV
WA_SECRETS_DIR=$WORK/secrets
WA_DATA_DIR=$DATA
COMPOSE_PROJECT_NAME=wa-obup
COMPOSE_FILE=docker-compose.yml:docker-compose.vault.yml:docker-compose.openbao.yml
ENV
cleanup() {
  local status=$?
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f .env .env.before-openbao27
  sudo rm -rf "$WORK" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT
fail() { echo "::error title=openbao upgrade::$*"; docker compose ps -a || true; docker compose logs --tail 40 openbao || true; exit 1; }
step() { printf "\n== %s\n" "$*"; }
bao() { docker compose exec -T -e BAO_TOKEN="$ROOT_TOKEN" openbao bao "$@"; }
status_field() { docker compose exec -T openbao bao status 2>/dev/null | awk -v k="$1" '$1==k{print $NF}'; }

bash scripts/init-secrets.sh >/dev/null
mkdir -p "$DATA/openbao/file" "$DATA/openbao/raft"
chmod -R 777 "$DATA/openbao"

step "a vault on OpenBao 2.6 with file storage, holding a secret"
docker compose up -d openbao || fail "OpenBao 2.6 would not start"
for _ in $(seq 1 30); do rc=0; docker compose exec -T openbao bao status >/dev/null 2>&1 || rc=$?; [ "$rc" -ne 1 ] && break; sleep 2; done
init="$(docker compose exec -T openbao bao operator init -key-shares=1 -key-threshold=1 -format=json)"
KEY="$(printf '%s' "$init" | python3 -c 'import json,sys; print(json.load(sys.stdin)["unseal_keys_b64"][0])')"
ROOT_TOKEN="$(printf '%s' "$init" | python3 -c 'import json,sys; print(json.load(sys.stdin)["root_token"])')"
docker compose exec -T openbao bao operator unseal "$KEY" >/dev/null
bao secrets enable -path=secret kv-v2 >/dev/null
bao kv put secret/world-assistant tfl_app_key=kept-across >/dev/null
[ "$(status_field Storage)" = file ] || fail "expected file storage to start with"
printf '%s\n' "$KEY" > "$WORK/secrets/openbao_unseal_key.txt"   # stored: it unseals by itself
docker compose up -d openbao-unseal

step "a failed upgrade goes back to file storage by itself"
if sudo -E WA_OPENBAO_FAIL_AT=started bash scripts/openbao.sh upgrade --yes; then fail "the failure was not reported"; fi
grep -q '^OPENBAO_CONFIG=' .env && fail ".env was not put back"
for _ in $(seq 1 20); do [ "$(status_field Sealed)" = false ] && break; sleep 3; done
[ "$(bao kv get -field=tfl_app_key secret/world-assistant)" = kept-across ] || fail "the secret is not there after going back"
sudo rm -rf "$DATA/openbao/raft"/*   # as the message says: delete it before trying again

step "the upgrade"
sudo -E bash scripts/openbao.sh upgrade --yes || fail "the upgrade failed"
{ grep -qx 'OPENBAO_VERSION=2.7.2' .env && grep -qx 'OPENBAO_CONFIG=openbao-raft.hcl' .env; } || fail ".env not set for 2.7"
[ "$(status_field Version)" = 2.7.2 ] || fail "not on 2.7.2"
[ "$(status_field Storage)" = raft ] || fail "not on raft storage"
[ "$(status_field Sealed)" = false ] || fail "not unsealed"
[ "$(bao kv get -field=tfl_app_key secret/world-assistant)" = kept-across ] || fail "the secret did not come across"
sudo -E bash scripts/openbao.sh upgrade --yes | grep -q "Already on OpenBao 2.7.2" || fail "a second run should do nothing"
echo "openbao upgrade: all checks passed"

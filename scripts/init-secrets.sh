#!/usr/bin/env bash
# Creates the secrets directory (WA_SECRETS_DIR, outside the shared folder by
# default) with a generated Postgres password and empty placeholders for the
# keys you must fill in yourself. Safe to re-run: existing files are kept.
set -euo pipefail
cd "$(dirname "$0")/.."

# Use the same location compose will read from.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
DIR="${WA_SECRETS_DIR:-/mnt/user/appdata/world-assistant/secrets}"

mkdir -p "$DIR"
# Directory is private to this user; files are readable so the unprivileged
# container user (uid 10001) can read the bind-mounted secret.
chmod 700 "$DIR"

gen() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    python3 -c 'import secrets; print(secrets.token_hex(24))'
  fi
}

make_secret() {
  local file="$DIR/$1.txt"
  if [ -e "$file" ]; then
    echo "kept     $file"
  else
    printf '%s' "$2" > "$file"
    echo "created  $file"
  fi
  chmod 644 "$file"
}

make_secret postgres_password "$(gen)"
make_secret nh_subscription_key ""
make_secret nh_subscription_key_secondary ""
make_secret mqtt_password ""
make_secret api_token "$(gen)"
make_secret fuel_finder_client_id ""
make_secret fuel_finder_client_secret ""
make_secret tfl_app_key ""
make_secret nr_access_token ""
make_secret skiddle_api_key ""
make_secret ticketmaster_api_key ""
make_secret pollen_api_key ""
make_secret redis_password "$(gen)"
# Only needed if you use a Vault / OpenBao key vault (docs/secrets.md); leave empty otherwise.
make_secret vault_token ""
make_secret vault_role_id ""
make_secret vault_secret_id ""

echo
echo "Next: put your National Highways primary and secondary keys into"
echo "  $DIR/nh_subscription_key.txt"
echo "  $DIR/nh_subscription_key_secondary.txt"
echo "and the Fuel Finder credentials into"
echo "  $DIR/fuel_finder_client_id.txt"
echo "  $DIR/fuel_finder_client_secret.txt"
echo "  $DIR/tfl_app_key.txt   (optional: TfL works without it at a low rate)"
echo "  $DIR/nr_access_token.txt   (optional: National Rail departures)"
echo "Edit them on the server (e.g. with nano); don't share the keys in chat."
echo
echo "The API token (needed to open the status page) is in:"
echo "  $DIR/api_token.txt"

echo
echo "Check what is set (never prints values), once the stack is up:"
echo "  docker compose exec api python -m app.secretsctl check"

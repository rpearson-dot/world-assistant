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
# Your broker's password, if it has one. With the bundled broker (WA_MQTT_HOST=wa-mqtt) one is generated.
if [ "${WA_MQTT_HOST:-}" = "wa-mqtt" ]; then
  make_secret mqtt_password "$(gen)"
  [ -s "$DIR/mqtt_password.txt" ] || { gen | tr -d '\n' > "$DIR/mqtt_password.txt"; echo "filled   $DIR/mqtt_password.txt"; }
else
  make_secret mqtt_password ""
fi
make_secret api_token "$(gen)"
make_secret fuel_finder_client_id ""
make_secret fuel_finder_client_secret ""
make_secret tfl_app_key ""
make_secret nr_access_token ""
make_secret skiddle_api_key ""
make_secret bods_api_key ""
make_secret tfwm_app_id ""
make_secret tfwm_app_key ""
make_secret octopus_api_key ""
make_secret ticketmaster_api_key ""
make_secret pollen_api_key ""
make_secret redis_password "$(gen)"
# Only needed with docker-compose.graph.yml (the knowledge graph); harmless otherwise.
# Neo4j's image wants "user/password" in one file, so it is written from the same password.
make_secret graph_password "$(gen)"
if [ ! -e "$DIR/neo4j_auth.txt" ]; then
  printf 'neo4j/%s' "$(cat "$DIR/graph_password.txt")" > "$DIR/neo4j_auth.txt"
  echo "created  $DIR/neo4j_auth.txt"
  chmod 644 "$DIR/neo4j_auth.txt"
else
  echo "kept     $DIR/neo4j_auth.txt"
fi
# Only needed with docker-compose.tailscale.yml: an auth key from the Tailscale admin console (first start only).
make_secret tailscale_authkey ""
# Only needed if you use a Vault / OpenBao key vault (docs/secrets.md); leave empty otherwise.
make_secret vault_token ""
make_secret vault_role_id ""
make_secret vault_secret_id ""

echo
echo "Every key is optional: a feature without its key says it is not set up. Put the ones you want in"
echo "(the status page's setup checklist says what each unlocks and where to get it):"
echo "  $DIR/nh_subscription_key.txt and nh_subscription_key_secondary.txt   National Highways roads"
echo "  $DIR/fuel_finder_client_id.txt and fuel_finder_client_secret.txt      fuel prices"
echo "  $DIR/tfl_app_key.txt          TfL (works without it at a low rate)"
echo "  $DIR/nr_access_token.txt      National Rail departures"
echo "  $DIR/bods_api_key.txt         live bus positions"
echo "  $DIR/skiddle_api_key.txt, ticketmaster_api_key.txt   events"
echo "  $DIR/pollen_api_key.txt       pollen"
echo "  $DIR/octopus_api_key.txt      your Octopus tariff and Saving Sessions"
echo "Edit them on the server (e.g. with nano); don't share the keys in chat. scripts/setup.sh asks for them instead."
echo
echo "From your home network the web pages need no token. From elsewhere, the token is in:"
echo "  $DIR/api_token.txt"

echo
echo "Check what is set (never prints values), once the stack is up:"
echo "  docker compose exec api python -m app.secretsctl check"

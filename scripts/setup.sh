#!/usr/bin/env bash
# First-time setup, asked as questions. Safe to re-run: press Enter to keep what is there.
#
#   bash scripts/setup.sh              asks, writes .env and the secret files, then starts the stack
#   bash scripts/setup.sh --no-start   only writes the files
#
# It never prints a key, and keys are typed without echo. Everything it writes can also be edited by hand
# afterwards: .env (settings) and the files in the secrets folder (one key per file).
set -euo pipefail
cd "$(dirname "$0")/.."

START=1
[ "${1:-}" = "--no-start" ] && START=0

say() { printf '%s\n' "$*"; }
hr() { printf '\n== %s\n' "$*"; }

# ask "question" "default" -> the answer (the default on Enter)
ask() {
  local ans=""
  if [ -n "$2" ]; then read -r -p "$1 [$2]: " ans || true; else read -r -p "$1: " ans || true; fi
  printf '%s' "${ans:-$2}"
}

# secret_ask "question" -> what was typed (no echo); empty means keep
secret_ask() {
  local ans=""
  read -r -s -p "$1: " ans || true
  printf '\n' >&2
  printf '%s' "$ans"
}

# env_get KEY -> its value in .env ("" if unset or commented out)
env_get() { if [ -f .env ]; then sed -n "s/^$1=//p" .env | tail -n 1; fi; }

# env_or KEY DEFAULT -> its value in .env, or DEFAULT
env_or() {
  local v
  v="$(env_get "$1")"
  printf '%s' "${v:-$2}"
}

# env_set KEY VALUE: replaces the live line (the last, as compose reads it), else the commented-out example,
# else appends
env_set() {
  local tmp live
  tmp="$(mktemp)"
  live="$(grep -c "^$1=" .env || true)"
  awk -v k="$1" -v v="$2" -v live="$live" '
    BEGIN { seen = 0; done = 0 }
    live > 0 && $0 ~ "^" k "=" { seen++; if (seen == live) { print k "=" v; done = 1 } ; next }
    live == 0 && !done && $0 ~ "^# ?" k "=" { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' .env > "$tmp"
  cat "$tmp" > .env
  rm -f "$tmp"
}

hr "World Assistant setup"
if [ ! -f .env ]; then
  cp .env.example .env
  say "Created .env from .env.example."
else
  say "Using your existing .env (Enter keeps each current value)."
fi

hr "Where things live"
data="$(ask "Folder for the database and backups" "$(env_or WA_DATA_DIR ./data)")"
secrets="$(ask "Folder for the keys (keep it private)" "$(env_or WA_SECRETS_DIR ./secrets)")"
port="$(ask "Port for the web pages" "$(env_or WA_API_PORT 8010)")"
env_set WA_DATA_DIR "$data"
env_set WA_SECRETS_DIR "$secrets"
env_set WA_API_PORT "$port"
mkdir -p "$data/backups"
# The app runs as uid 10001 and writes backups here (as scripts/deploy.sh sets it up).
chown 10001:10001 "$data/backups" 2>/dev/null || chmod 777 "$data/backups"
chmod 770 "$data/backups" 2>/dev/null || true

hr "Home Assistant"
host_now="$(env_get WA_MQTT_HOST)"
case "$host_now" in
  wa-mqtt) choice_now=2 ;;
  "") choice_now=3 ;;
  *) choice_now=1 ;;
esac
say "World Assistant sends its sensors to Home Assistant through an MQTT broker."
say "  1) Home Assistant already has a broker (its Mosquitto add-on, or your own container)"
say "  2) Run a broker here for it"
say "  3) I don't use Home Assistant (you can change this later)"
mq="$(ask "Choose 1, 2 or 3" "$choice_now")"
files="$(env_or COMPOSE_FILE docker-compose.yml)"
# keep any other overlays (vault, graph, traefik); the broker and routing ones are decided below
files="$(printf '%s' "$files" | tr ':' '\n' | grep -v -e '^docker-compose.mqtt.yml$' -e '^docker-compose.routing.yml$' | paste -sd: - || true)"
files="${files:-docker-compose.yml}"
case "$mq" in
  1)
    mhost="$(ask "Broker address (an IP; container names do not resolve across Docker networks)" "$([ "$host_now" = wa-mqtt ] || printf '%s' "$host_now")")"
    env_set WA_MQTT_HOST "$mhost"
    env_set WA_MQTT_PORT "$(ask "Broker port" "$(env_or WA_MQTT_PORT 1883)")"
    env_set WA_MQTT_USER "$(ask "Broker user (empty if none)" "$(env_get WA_MQTT_USER)")"
    ;;
  2)
    env_set WA_MQTT_HOST wa-mqtt
    env_set WA_MQTT_USER wa
    env_set WA_MQTT_PUBLISH_PORT "$(ask "Port Home Assistant connects to" "$(env_or WA_MQTT_PUBLISH_PORT 1883)")"
    files="$files:docker-compose.mqtt.yml"
    ;;
  *)
    env_set WA_MQTT_HOST ""
    ;;
esac

hr "Road routing (optional)"
has_routing=n
case ":$(env_get COMPOSE_FILE):" in *:docker-compose.routing.yml:*) has_routing=y ;; esac
say "Valhalla turns your trips into real road routes, so closures, fuel and conditions follow them."
say "It downloads about 2 GB and builds for a while the first time."
routing="$(ask "Add it? (y/n)" "$has_routing")"
case "$routing" in y|Y|yes) files="$files:docker-compose.routing.yml" ;; esac
env_set COMPOSE_FILE "$files"

hr "Secret files"
made="$(bash scripts/init-secrets.sh)"
printf '%s\n' "$made" | grep -E '^(created|filled)' || true
dir="$secrets"
if [ "$mq" = 1 ]; then
  pw="$(secret_ask "Broker password (Enter to keep or leave empty)")"
  [ -n "$pw" ] && { printf '%s' "$pw" > "$dir/mqtt_password.txt"; chmod 644 "$dir/mqtt_password.txt"; }
fi

hr "Keys (all optional, all free)"
say "Paste a key and press Enter, or just press Enter to skip (or keep the one you have)."
say "Nothing you paste is shown. Each line says what it unlocks and where to get the key."
key() {  # key FILE "what it unlocks" "where"
  local f="$dir/$1.txt" state="not set" v
  [ -s "$f" ] && state="set"
  say ""
  say "$2 ($state)"
  say "  get one at: $3"
  v="$(secret_ask "  $1")"
  if [ -n "$v" ]; then
    printf '%s' "$v" > "$f"
    chmod 644 "$f"
    say "  saved."
  fi
}
key nh_subscription_key "National Highways: closures and speed limits on motorways and major A roads" "https://developer.data.nationalhighways.co.uk/"
key nh_subscription_key_secondary "National Highways secondary key (same page; optional)" "https://developer.data.nationalhighways.co.uk/"
key fuel_finder_client_id "Fuel Finder: every UK station's prices (client id)" "https://www.gov.uk/guidance/access-the-latest-fuel-prices-and-forecourt-data-via-api-or-email"
key fuel_finder_client_secret "Fuel Finder (client secret)" "the same page"
key tfl_app_key "TfL: London buses, the bus tracker, train boards (works without, slower)" "https://api-portal.tfl.gov.uk/"
key nr_access_token "National Rail: live departures from any station" "https://raildata.org.uk/"
key bods_api_key "Bus Open Data Service: live bus positions anywhere in Britain" "https://data.bus-data.dft.gov.uk/"
key skiddle_api_key "Skiddle: what is on near you" "https://www.skiddle.com/api/"
key ticketmaster_api_key "Ticketmaster: concerts, sport and shows near you" "https://developer.ticketmaster.com/"
key pollen_api_key "Polleninformation: pollen forecasts (personal use, 40 requests a day)" "https://www.polleninformation.at/en/data-interface"
key octopus_api_key "Octopus Energy: your tariff and Saving Sessions" "https://octopus.energy/dashboard/new/accounts/personal-details/api-access"
if [ -s "$dir/octopus_api_key.txt" ]; then
  env_set WA_OCTOPUS_ACCOUNT "$(ask "  Octopus account number (A-1234ABCD)" "$(env_get WA_OCTOPUS_ACCOUNT)")"
fi

addr="$(hostname -I 2>/dev/null | awk '{print $1}')"
addr="${addr:-this-server}"
hr "Done"
say "Settings are in .env; keys are in $dir (one per file)."
if [ "$START" = 0 ]; then
  say "Start it with: docker compose up -d --build"
  exit 0
fi

hr "Starting"
if [ -n "$(env_get WA_IMAGE)" ]; then docker compose up -d; else docker compose up -d --build; fi
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 && break
  sleep 3
done
if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
  say ""
  say "World Assistant is running: http://$addr:$port/status"
  say "  From your home network the pages need no token."
  say "  From elsewhere, use the token in $dir/api_token.txt."
  say "  The Status page's Setup list shows what is working; Home Assistant setup is at http://$addr:$port/ha"
  say "  Stops, stations and postcodes load in the next few minutes."
else
  say "It has not answered yet. See what happened with: docker compose logs --tail 50 api migrate"
  exit 1
fi

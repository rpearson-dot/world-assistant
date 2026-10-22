#!/usr/bin/env bash
# Your own certificate authority for MQTT over TLS, and a certificate for your broker signed by it.
# No domain, no DNS provider, no 90-day renewals: the CA lasts 10 years, the broker certificate 5 (re-run to renew).
#
#   bash scripts/mqtt-ca.sh mqtt.example.com 203.0.113.7 192.168.1.10
#   bash scripts/mqtt-ca.sh --out /mnt/user/appdata/mosquitto/certs --restart mosquitto mqtt.example.com 192.168.1.10
#
# Give every name and IP address a client uses to reach the broker: the public name or IP the car's OVMS module
# connects to, and the NAS's LAN address Home Assistant and World Assistant use. A client checks the certificate
# against the one it connected with.
#
# It writes:
#   <secrets>/mqtt-ca/ca.key, ca.pem    the CA (kept with your other secrets; the key never leaves this folder)
#   <out>/fullchain.pem, privkey.pem     for Mosquitto: certfile and keyfile
#   <out>/ca.pem                         the CA certificate every client is told to trust (it is not secret)
#   <data>/keys/mqtt-ca.pem              the same, where World Assistant reads it (WA_MQTT_TLS_CA=/keys/mqtt-ca.pem)
#
# Re-running issues a new broker certificate from the same CA, so the car and Home Assistant need no change.
# --new-ca starts a new CA (then every client must be given the new ca.pem).
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
OUT="$DATA/mosquitto/certs"  # the bundled broker's folder; --out for your own Mosquitto
DAYS=1825
RESTART=""
NEW_CA=0
NAMES=()

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --days) DAYS="$2"; shift 2 ;;
    --restart) RESTART="$2"; shift 2 ;;
    --new-ca) NEW_CA=1; shift ;;
    -h|--help) usage 0 ;;
    -*) echo "unknown option $1" >&2; usage 2 ;;
    *) NAMES+=("$1"); shift ;;
  esac
done
[ ${#NAMES[@]} -gt 0 ] || { echo "Give the names and IP addresses clients use to reach the broker." >&2; usage 2; }
command -v openssl >/dev/null || { echo "openssl is needed" >&2; exit 2; }

is_ip() { [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ || "$1" == *:*:* ]]; }
SAN=""
for n in "${NAMES[@]}"; do
  if is_ip "$n"; then SAN="$SAN,IP:$n"; elif [[ "$n" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then SAN="$SAN,DNS:$n";
  else echo "not a host name or IP address: $n" >&2; exit 2; fi
done
SAN="${SAN#,}"

CA_DIR="$SECRETS/mqtt-ca"
mkdir -p "$CA_DIR" "$OUT"
chmod 700 "$CA_DIR"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ "$NEW_CA" = 1 ] || [ ! -s "$CA_DIR/ca.key" ] || [ ! -s "$CA_DIR/ca.pem" ]; then
  [ -s "$CA_DIR/ca.pem" ] && echo "Starting a new CA: every client must be given the new ca.pem."
  ( umask 077; openssl genrsa -out "$CA_DIR/ca.key" 2048 2>/dev/null )
  openssl req -x509 -new -key "$CA_DIR/ca.key" -sha256 -days 3650 -subj "/CN=World Assistant MQTT CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" -out "$CA_DIR/ca.pem"
  echo "created  the CA: $CA_DIR/ca.pem (10 years)"
else
  echo "kept     the CA: $CA_DIR/ca.pem (until $(openssl x509 -enddate -noout -in "$CA_DIR/ca.pem" | cut -d= -f2))"
fi

# RSA 2048: what every MQTT client (the OVMS module's TLS library included) can use.
( umask 077; openssl genrsa -out "$WORK/privkey.pem" 2048 2>/dev/null )
openssl req -new -key "$WORK/privkey.pem" -subj "/CN=${NAMES[0]}" -out "$WORK/req.csr"
cat > "$WORK/ext.cnf" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$SAN
authorityKeyIdentifier=keyid
subjectKeyIdentifier=hash
EOF
openssl x509 -req -in "$WORK/req.csr" -CA "$CA_DIR/ca.pem" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
  -CAserial "$CA_DIR/ca.srl" -days "$DAYS" -sha256 -extfile "$WORK/ext.cnf" -out "$WORK/cert.pem" 2>/dev/null
openssl verify -CAfile "$CA_DIR/ca.pem" "$WORK/cert.pem" >/dev/null

cat "$WORK/cert.pem" "$CA_DIR/ca.pem" > "$OUT/fullchain.pem"
install -m 600 "$WORK/privkey.pem" "$OUT/privkey.pem"
install -m 644 "$CA_DIR/ca.pem" "$OUT/ca.pem"
# The official Mosquitto image runs as uid 1883 and must read its key.
chown 1883:1883 "$OUT/fullchain.pem" "$OUT/privkey.pem" 2>/dev/null || chmod 644 "$OUT/privkey.pem"
mkdir -p "$DATA/keys"
install -m 644 "$CA_DIR/ca.pem" "$DATA/keys/mqtt-ca.pem"

echo "issued   the broker certificate for: ${NAMES[*]} (until $(openssl x509 -enddate -noout -in "$WORK/cert.pem" | cut -d= -f2))"
echo "         $OUT/fullchain.pem and $OUT/privkey.pem"

if [ -n "$RESTART" ]; then
  docker restart "$RESTART" >/dev/null && echo "restarted $RESTART"
fi

cat <<EOF

Next:
 1. Mosquitto: a TLS listener using these files (paths as Mosquitto sees them), then restart it:
      listener 8883
      certfile /mosquitto/certs/fullchain.pem
      keyfile  /mosquitto/certs/privkey.pem
      tls_version tlsv1.2
    (The bundled broker picks them up from $DATA/mosquitto/certs by itself on restart.)
 2. OVMS: Tools > Editor, create /store/trustedca/world-assistant-ca.pem with the contents of $OUT/ca.pem,
    then Tools > Shell: tls trust reload. In Config > Server V3 (MQTT): port 8883, Enable TLS on.
 3. Home Assistant: MQTT integration > Reconfigure, port 8883, certificate validation Custom, upload ca.pem.
 4. World Assistant: Settings > MQTT, port 8883, Encrypted on (WA_MQTT_TLS_CA=/keys/mqtt-ca.pem is set for you
    if .env does not set it already). Settings > MQTT also offers ca.pem to download.
 Renew the broker certificate before $(openssl x509 -enddate -noout -in "$WORK/cert.pem" | cut -d= -f2): run this again.
EOF

# Point World Assistant at the CA, unless .env already says something.
if [ -f .env ] && ! grep -q '^WA_MQTT_TLS_CA=.' .env; then
  if grep -q '^#\? \?WA_MQTT_TLS_CA=' .env; then
    sed -i 's|^#\? \?WA_MQTT_TLS_CA=.*|WA_MQTT_TLS_CA=/keys/mqtt-ca.pem|' .env
  else
    echo "WA_MQTT_TLS_CA=/keys/mqtt-ca.pem" >> .env
  fi
  echo " (.env: WA_MQTT_TLS_CA=/keys/mqtt-ca.pem; docker compose up -d to apply)"
fi

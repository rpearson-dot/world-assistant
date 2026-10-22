# MQTT over TLS: from the car's OVMS module to Home Assistant

The car's OVMS module talks to your broker over mobile data, so the broker has to be reachable from the
internet. That makes TLS (encryption plus proof of who the broker is) and strong logins essential on the way
in. This guide sets up:

```
OVMS (car, 4G) --TLS, port 8883, over the internet--> your router (forwards 8883 only)
                                                          |
                                                          v
                              Mosquitto on the NAS: listener 8883 (TLS) + 1883 (home network only)
                                   |                         |
                     Home Assistant (TLS, 8883)      World Assistant (TLS, 8883)
```

You need: a domain name you control (or a free dynamic DNS name), Mosquitto (yours on Unraid, or the
bundled one in `docker-compose.mqtt.yml`), and the OVMS web interface.

## 1. A name for your broker

OVMS checks the broker's certificate against the name it connects to, so the broker needs a DNS name that
points at your home's public IP, for example `mqtt.example.com`. If your IP changes, use your DNS provider's
dynamic DNS (Cloudflare, DuckDNS and others; Unraid has containers for both).

Inside the house the same name should lead to the NAS's LAN address, so Home Assistant and World Assistant
can use it too (the certificate only matches the name). Either your router does that already ("NAT
loopback" or "hairpin NAT"), or add a local DNS entry for `mqtt.example.com` pointing at the NAS (in the
router, Pi-hole or AdGuard).

## 2. A certificate (Let's Encrypt)

Use a DNS-01 challenge: it proves you own the name through your DNS provider, so nothing extra has to be
opened to the internet (no port 80). On Unraid the simplest is the `acme.sh` container, or SWAG / Nginx Proxy
Manager if you already run one (they keep the certificates in their appdata folder). With `acme.sh` and
Cloudflare, for example:

```bash
docker run --rm -v /mnt/user/appdata/acme:/acme.sh -e CF_Token=<a Cloudflare API token for this zone> \
  neilpang/acme.sh --issue --dns dns_cf -d mqtt.example.com --keylength ec-256
docker run --rm -v /mnt/user/appdata/acme:/acme.sh -v /mnt/user/appdata/mosquitto/certs:/certs neilpang/acme.sh \
  --install-cert -d mqtt.example.com --ecc --fullchain-file /certs/fullchain.pem --key-file /certs/privkey.pem
```

Let's Encrypt certificates last 90 days. Renew on a schedule (Unraid's User Scripts plugin, weekly:
`... neilpang/acme.sh --cron`, then the `--install-cert` line again) and **restart Mosquitto afterwards** so it
picks up the new certificate. Mosquitto must be able to read both files (`chown` them to the user it runs as;
in the official image that is uid 1883).

No domain? A private certificate authority works too (OVMS, Home Assistant and World Assistant can all be
given your own CA), but then every client needs that CA file, and you manage its expiry yourself.

## 3. Mosquitto

Add a TLS listener and keep the plain one for the home network only. In `mosquitto.conf`:

```
# home network only: never forward this port
listener 1883
# from the internet, through the router: TLS only
listener 8883
certfile /mosquitto/certs/fullchain.pem
keyfile /mosquitto/certs/privkey.pem
tls_version tlsv1.2

per_listener_settings false
allow_anonymous false
password_file /mosquitto/config/passwd
acl_file /mosquitto/config/acl
```

Give each client its own login (`mosquitto_passwd /mosquitto/config/passwd ovms`, and likewise
`homeassistant` and `worldassistant`), with long random passwords. An ACL file limits what each may do, so a
stolen car login can only touch the car's topics:

```
user ovms
topic readwrite ovms/#

user homeassistant
topic readwrite #

user worldassistant
topic readwrite world_assistant/#
topic readwrite homeassistant/#
```

Map port 8883 in the Mosquitto container (Unraid: add a port mapping 8883 to 8883), restart it, and check
from a computer on the home network:

```bash
openssl s_client -connect mqtt.example.com:8883 -servername mqtt.example.com </dev/null | head -20
# "Verify return code: 0 (ok)" and the subject CN=mqtt.example.com
mosquitto_sub -h mqtt.example.com -p 8883 --capath /etc/ssl/certs -u homeassistant -P '<password>' -t 'ovms/#' -v
```

**The bundled broker** does the TLS part for you: put `fullchain.pem` and `privkey.pem` in
`WA_DATA_DIR/mosquitto/certs` and restart it. It then also listens on 8883 (published as
`WA_MQTT_TLS_PUBLISH_PORT`). For per-client logins and an ACL, run your own Mosquitto as above.

## 4. The router

Forward **TCP 8883 only** to the NAS's port 8883. Never forward 1883: it is unencrypted. Nothing else needs
opening; World Assistant itself stays behind the router (reach it over Tailscale, [tailscale.md](tailscale.md)).

## 5. The OVMS module

In the OVMS web interface: **Config > Server V3 (MQTT)**:

| Setting | Value |
| --- | --- |
| Server | `mqtt.example.com` |
| Port | `8883` |
| Enable TLS | on |
| Username / Password | the `ovms` login |
| Topic prefix | `ovms/<username>/<vehicle id>/` (the default pattern; keep it, Home Assistant's OVMS integration expects it) |
| Enable auto-start | yes |

Save, then check **Status**: Server V3 should say connected. The module needs the right time to check a
certificate, which it gets from the modem or GPS.

If it reports a certificate error, the module does not trust the root behind your certificate. For Let's
Encrypt that is **ISRG Root X1** (from letsencrypt.org/certificates, the PEM file): in **Tools > Editor** create
`/store/trustedca/isrgrootx1.pem` with its contents, then in **Tools > Shell** run `tls trust reload` (and
`tls trust list` to see it). For a private CA, put your CA's certificate there instead.

## 6. Home Assistant

In **Settings > Devices & services**, open the MQTT integration and choose **Reconfigure**: broker
`mqtt.example.com`, port `8883`, the `homeassistant` login. The TLS settings are under the advanced options
(turn on **Advanced mode** in your user profile to see them): **Broker certificate validation: Auto** (a Let's
Encrypt certificate is trusted already; for a private CA choose Custom and upload it), transport TCP.
`mqtt.example.com` must lead to the NAS from inside the house (step 1).

For the car itself, the OVMS integration (from HACS) subscribes to `ovms/#` through this same connection.

## 7. World Assistant

In `.env`:

```
WA_MQTT_HOST=mqtt.example.com
WA_MQTT_PORT=8883
WA_MQTT_USER=worldassistant
WA_MQTT_TLS=true
# only for a private CA: the CA file's path inside the containers (mount it with a compose override)
# WA_MQTT_TLS_CA=/certs/my-ca.pem
```

and the password in `mqtt_password.txt`. If the containers cannot resolve the name to the NAS (no NAT loopback
or local DNS), add it in a `docker-compose.override.yml`:

```yaml
services:
  worker:
    extra_hosts: ["mqtt.example.com:192.168.1.10"]
```

`docker compose up -d --force-recreate`, then the Settings page's Home Assistant line shows the last
publish (or the TLS error, if the certificate or name does not match).

## Why not without an open port?

The OVMS module cannot join a tailnet, so something has to be reachable from the internet. The alternatives
move that something elsewhere: a hosted MQTT broker that both the car and your home broker connect out to
(Mosquitto can bridge to it), or an OVMS community server (the car then talks to someone else's server).
With TLS, an ACL and per-client passwords, a single forwarded 8883 is a reasonable choice for a home.

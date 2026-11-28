# Car charger (OCPP)

World Assistant can be your charger's **central system**: the server an OCPP charger connects to and takes its
orders from. It keeps everything the charger says, so a charger that restarts itself, drops off the network or
reports a fault can be seen doing it. It starts and stops charging, and it shows the charger in Home Assistant, where
the charging plan uses it.

The page is **Vehicles → Charger** (`/charger`).

## Moving the charger to World Assistant

A charger talks to one central system at a time. Moving it to World Assistant means Home Assistant's OCPP integration
stops seeing it; World Assistant publishes the charger to Home Assistant instead.

1. Switch the module on (Settings → Modules → Car charger).
2. In the charger's own settings (for the Sync EV, its local web page or installer app), set the OCPP server address
   to `ws://<your World Assistant server>:8010/ocpp/`. Most chargers add their own id to the end (the Sync EV's is
   `U1HKP`); if yours does not, use `ws://<server>:8010/ocpp/U1HKP`. OCPP 1.6J, no TLS, no password.
3. The charger connects within a minute or two. The page shows "Connected", and its first start-up.
4. On the page, under Settings, put the charger's id in "Chargers allowed to connect", so nothing else can.
5. In Home Assistant, remove or disable the OCPP integration, so it does not compete with World Assistant for the
   charger. World Assistant's own planner drives charging (see the Charger page); Home Assistant only shows its state.

To go back, point the charger at Home Assistant's address again.

Connections are accepted only from the home network: never through Tailscale or any other proxy.

## What is recorded

| | |
|---|---|
| Every message | Both ways, exactly as sent, kept two years (Settings → More → the last 100 messages) |
| What happened | Connected and disconnected (with how, and how long it had been quiet), started up (a **restart** while still connected is told from a first start), faults with the charger's own codes, status changes, every command and its answer |
| Sessions | Start and stop, meter readings, energy, and why each stopped |

A notification ("Car, charger and home" in Settings → Notifications) goes out when the charger restarts, reports a
fault, or has been silent for 15 minutes.

## Charging: Off, On, Auto

One switch at the top of the page (and `select.wa_ocpp_mode` in Home Assistant):

- **Off:** the charger is made unavailable (OCPP ChangeAvailability Inoperative), after stopping any session. It
  will not charge even with the car plugged in.
- **On:** charges whenever the car is plugged in: a session is started now if the car is waiting, and again each
  time it is plugged in. Home Assistant's plan cannot stop it.
- **Auto:** Home Assistant's charging plan starts and stops it through `switch.wa_ocpp_charging` (cheap hours,
  free sessions, your target). If the charger stops by itself while the plan wants it charging (a fault, a restart)
  and comes back ready, World Assistant starts it again straight away rather than waiting for the plan.

In Off and On, Home Assistant's start and stop are refused and recorded ("not done, the charger is set to On").
The setting is kept and applied again whenever the charger reconnects. Everything else (allowed chargers, meter
readings, restarts, configuration, the raw messages) is under "Settings and tools".

- While it charges, World Assistant asks for meter readings every minute (the Sync EV never sends them by itself).
- The Sync EV on its 2020 firmware has no current setting, so charging is start and stop at about 4.6 kW.

## Server WebSocket settings (needed for control)

The charger connects over a WebSocket, and two of the web server's defaults stop it **receiving** anything World
Assistant sends — so the charger's own messages (boot, status, meter) are recorded fine, but every command
(RemoteStart/Stop, ChangeAvailability, charging profiles) comes back "no answer within 30 s". The API process is
therefore started by `app/serve.py`, which runs uvicorn with both turned off; `docker-compose.yml` must keep using
the image's default command (do not override it with a bare `uvicorn …`), and the setting is guarded by a test in
`tests/test_ocpp.py`.

- **WebSocket compression (permessage-deflate) off.** uvicorn's default compresses each frame with a ~32 KB window.
  Some chargers — the Sync EV among them — cannot decompress that and silently drop every frame, so no command is ever
  received. OCPP-J messages are tiny, so compression gains nothing. (Home Assistant's OCPP server works only because
  the `websockets` library it uses negotiates a small 4 KB window by default.)
- **Automatic WebSocket pings off.** uvicorn pings every 20 s and drops the link if no pong arrives in 20 s; a charger
  that mishandles ping control frames is disrupted. Liveness is kept by OCPP Heartbeat (every 5 minutes) and World
  Assistant's own connection tracking instead. (Home Assistant's OCPP server disables automatic pings for the same
  reason.)

If control ever stops working after a server or dependency change, check these two first: point the charger at World
Assistant and watch **Logs → Charger** — a command answered with Accepted/Rejected means the charger is receiving us;
"no answer" every time means it is not, and compression is the first suspect.

## Two backends: built-in or ocpp2mqtt

The **backend** is who the charger connects to as its OCPP central system. You can switch between two, live on the
Charger page (Settings → Charger backend) with no redeploy — both run at once, and the switch chooses which one is in
charge. Either way the Charger page, the record, the master switch and the Home Assistant entities work the same,
because both write into the same tables.

- **Built-in** (the default): World Assistant's own WebSocket server (`app/modules/ocpp/server.py`). The charger
  connects to `ws://<server>:8010/ocpp/<id>` as above. Fewest moving parts, but it depends on the WebSocket settings
  above, and some chargers never answer its commands (see below).
- **ocpp2mqtt**: a dedicated container ([gyzod/ocpp2mqtt](https://github.com/gyzod/ocpp2mqtt)) is the central system
  the charger connects to; it runs the proven `ocpp` + `websockets` stack (the same one Home Assistant uses) and
  speaks to World Assistant over MQTT. World Assistant reads its state topics and drives it over the same broker
  (`app/modules/ocpp/bridge.py`). Use this when the built-in server connects and records fine but the charger ignores
  every command — the Sync EV behaves this way, and ocpp2mqtt's stack is what makes it obey.

To use ocpp2mqtt:

1. Bring up the container alongside World Assistant and an MQTT broker:
   `docker compose -f docker-compose.yml -f docker-compose.mqtt.yml -f docker-compose.ocpp2mqtt.yml up -d`
   (or add `docker-compose.ocpp2mqtt.yml` to `COMPOSE_FILE` in `.env`). Set `WA_OCPP2MQTT_CPID` to your charge point
   ID (default `U1HKP`); pin `WA_OCPP2MQTT_IMAGE` to a digest once deployed.
2. Point the charger's OCPP 1.6J central-system address at `ws://<server>:${WA_OCPP2MQTT_PORT:-3000}/<id>`
   (e.g. `ws://10.0.2.49:3000/U1HKP`) instead of the `:8010/ocpp/` address.
3. On the Charger page, set **Charger backend** to *ocpp2mqtt*. Keep the **ocpp2mqtt base path** equal to the
   container's `WA_OCPP2MQTT_BASE` (default `wa/ocpp`); the container publishes under `<base>/<id>/state/...` and
   listens for commands on `<base>/<id>/cmd`.

The command's answer comes back asynchronously over MQTT (shown in Logs → Charger as a command result), so the page
reports that a command was sent rather than waiting for the charger's reply.

**How control works on this backend.** ocpp2mqtt is itself the central system, and it has its own `charging_enabled`
gate (default OFF): it authorises a `StartTransaction` only while that gate is ON *and* the id tag is in
`AUTHORIZED_TAG_ID_LIST` (the overlay sets `["WorldAssistant"]`, the tag World Assistant starts charges with).
So World Assistant drives the gate — ON before every RemoteStart, OFF after every stop and for Off/Auto — rather than
`ChangeAvailability`, which this charger ignores. In short, the lever is `charging_enabled` + RemoteStart/Stop. The
charger does not free-vend, so leaving it blocked keeps it idle until World Assistant (or the planner, in Auto) starts
a charge. If you ever see a charge start and then stop at once with the charger's `StartTransaction` answered
`Blocked`, the gate was off — check that the id tag matches `AUTHORIZED_TAG_ID_LIST`.

## Home Assistant

Over MQTT, as the device "Charger U1HKP": `sensor.wa_ocpp_status`, `sensor.wa_ocpp_power` (kW),
`sensor.wa_ocpp_energy` (kWh meter), `sensor.wa_ocpp_session_energy`, `sensor.wa_ocpp_current`,
`sensor.wa_ocpp_error`, `sensor.wa_ocpp_last_boot`, `binary_sensor.wa_ocpp_connected`, `switch.wa_ocpp_charging`
(on starts a session, off stops it; obeyed in Auto) and `select.wa_ocpp_mode` (Off, On, Auto).

## API

| | |
|---|---|
| `GET /v1/ocpp` | every charger, its state, the week's restarts, drops and faults, and its current session |
| `PUT /v1/ocpp/settings` | allowed chargers, meter reading interval, Home Assistant on or off |
| `PUT /v1/ocpp/mode` | `{"mode": "off" \| "on" \| "auto"}`, applied to every connected charger |
| `PUT /v1/ocpp/backend` | `{"backend": "builtin" \| "ocpp2mqtt", "mqtt_base"?: "wa/ocpp"}`, switch the backend live |
| `POST /v1/ocpp/{id}/start`, `/stop` | start or stop a session once |
| `POST /v1/ocpp/{id}/command` | `{"action": "Reset", "args": {"type": "Soft"}}`, GetConfiguration, ChangeConfiguration, TriggerMessage, UnlockConnector, ChangeAvailability, ClearCache |
| `GET /v1/ocpp/{id}/events`, `/messages`, `/sessions` | the record |

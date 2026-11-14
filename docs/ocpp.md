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
5. In Home Assistant, remove or disable the OCPP integration. The charging plan (`wa_ev_plan.yaml`) uses World
   Assistant's charger entities by itself once they appear, unless the plan's entity fields name others.

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

## Charging

- **Start charging** sends OCPP RemoteStartTransaction on connector 1; **Stop charging** stops the session the
  charger last said it started.
- While it charges, World Assistant asks for meter readings every minute (the Sync EV never sends them by itself).
- The Sync EV on its 2020 firmware has no current setting, so charging is start and stop at about 4.6 kW.

## Home Assistant

Over MQTT, as the device "Charger U1HKP": `sensor.wa_ocpp_status`, `sensor.wa_ocpp_power` (kW),
`sensor.wa_ocpp_energy` (kWh meter), `sensor.wa_ocpp_session_energy`, `sensor.wa_ocpp_current`,
`sensor.wa_ocpp_error`, `sensor.wa_ocpp_last_boot`, `binary_sensor.wa_ocpp_connected` and `switch.wa_ocpp_charging`
(on starts a session, off stops it).

## API

| | |
|---|---|
| `GET /v1/ocpp` | every charger, its state, the week's restarts, drops and faults, and its current session |
| `PUT /v1/ocpp/settings` | allowed chargers, meter reading interval, Home Assistant on or off |
| `POST /v1/ocpp/{id}/start`, `/stop` | start or stop charging |
| `POST /v1/ocpp/{id}/command` | `{"action": "Reset", "args": {"type": "Soft"}}`, GetConfiguration, ChangeConfiguration, TriggerMessage, UnlockConnector, ChangeAvailability, ClearCache |
| `GET /v1/ocpp/{id}/events`, `/messages`, `/sessions` | the record |

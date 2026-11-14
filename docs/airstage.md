# Air conditioning (Fujitsu Airstage)

World Assistant talks to Fujitsu Airstage air conditioners directly on your home network, the same way the
Airstage app does on Wi-Fi, never through Fujitsu's cloud. It replaces Home Assistant's Airstage integration.

The page is **Energy → Air conditioning** (`/aircon`): each unit with the temperature inside and out, an Off/On
switch, the mode (Auto, Heat, Cool, Dry, Fan), the target (half degrees) and the fan, plus swing, Economy, Powerful,
the quiet outdoor unit and 10° minimum heat where the unit has them.

## Setting it up

1. Give each unit's Wi-Fi adapter a fixed address in your router.
2. Find each unit's **device id**: in the Airstage app, the unit, then its information; or on the label of the unit's
   Wi-Fi adapter (12 letters and digits; colons are fine).
3. Switch the module on (Settings → Modules), open the page, and under **Units** add each one: a name, its
   address and its device id. Save. The units are read within two minutes.

Only addresses on your home network are accepted.

## What it does

- Reads every unit every two minutes and keeps the readings (on, mode, target, inside and outside, fan, energy)
  for two years. A unit that does not answer is recorded as such.
- A notification ("Car, charger and home") for an error code, a filter that needs cleaning, or a unit that has not
  answered for half an hour.
- Home Assistant: each unit as a climate entity, `climate.wa_aircon_<name>` (modes off, auto, cool, dry, fan_only,
  heat; target; fan; swing), with the inside temperature. Changes made in Home Assistant (or Apple Home through it)
  come back to the unit through World Assistant.
- The assistant: "what are the air conditioners doing" and "set the bedroom to 21".

The unit's own API was worked out from [pyairstage](https://github.com/danielkaldheim/pyairstage) (MIT licence), the
library Home Assistant's integration uses.

## API

| | |
|---|---|
| `GET /v1/airstage` | every unit and its state |
| `PUT /v1/airstage/units` | `[{"name": "Bedroom", "ip": "10.0.2.150", "device_id": "A0B1C2D3E4F5"}]` |
| `POST /v1/airstage/{unit}/set` | any of `power`, `mode`, `target_c`, `fan`, `swing_vertical`, `swing_horizontal`, `economy`, `powerful`, `low_noise`, `minimum_heat` |
| `GET /v1/airstage/{unit}/history?hours=24` | the readings |

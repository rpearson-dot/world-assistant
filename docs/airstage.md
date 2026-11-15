# Air conditioning (Fujitsu Airstage)

World Assistant talks to Fujitsu Airstage air conditioners directly on your home network, the same way the
Airstage app does on Wi-Fi, never through Fujitsu's cloud. It replaces Home Assistant's Airstage integration.

The page is **In the home → Air conditioning** (`/aircon`): each unit with the temperature inside and out, an Off/On
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
| `POST /v1/airstage/units` | add one: `{"name": "Bedroom", "ip": "10.0.2.150", "device_id": "A0B1C2D3E4F5"}` |
| `PATCH /v1/airstage/units/{id}` | rename or change one unit's address/device id; its id (so its history and Home Assistant entity) stays |
| `DELETE /v1/airstage/units/{id}` | remove one unit (its Home Assistant entity is retracted) |
| `PUT /v1/airstage/units` | replace the whole set; send each unit's `id` back to keep it |
| `POST /v1/airstage/{unit}/set` | any of `power`, `mode`, `target_c`, `fan`, `swing_vertical`, `swing_horizontal`, `economy`, `powerful`, `low_noise`, `minimum_heat` |
| `POST /v1/airstage/set-all` | the same change applied to every unit; each unit on its own, so one that refuses or does not answer is reported and the rest still change |
| `GET /v1/airstage/{unit}/history?hours=24` | the readings |

A unit's id is minted once (`u` + six hex) and never derived from its name, so renaming a unit on the Air conditioning
page keeps its reading history and its Home Assistant `climate.wa_aircon_<id>` entity. Edit a unit on its card; the
"Add or edit units" box adds new ones.

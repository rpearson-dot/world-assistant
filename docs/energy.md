# Energy supply

World Assistant reports what electricity costs now and later. **Home Assistant acts on it.** Nothing in
WA talks to an inverter or battery: WA is the outside world, HA owns the house. A Sunsynk inverter (for
example through the kellerza add-on) is driven from HA automations that read the sensors below.

## Rates

Reads your tariff's unit rates from Octopus (public, no key), every 30 minutes, for yesterday to three days
ahead. Works for fixed, time-of-use (Cosy, Go) and half-hourly (Agile) tariffs alike.

Bands are worked out from the rates themselves, not hard-coded times:

| Distinct prices | Result |
|---|---|
| 1 | all `standard` |
| 2 | lower `cheap`, higher `standard` |
| 3 (Cosy) | `cheap`, `standard`, `peak` |
| more (Agile) | `cheap` at or below the 25th percentile (and any negative price), `peak` at or above the 75th |

## Set-up

1. `bash scripts/init-secrets.sh` creates an empty `octopus_api_key` secret. Put your Octopus API key in it
   (Octopus dashboard → Developer settings), or use `bash scripts/openbao.sh import`.
2. In `.env` set `WA_OCTOPUS_ACCOUNT=A-XXXXXXXX`. WA reads your account to find the current import tariff
   (export agreements are ignored).
   *No key?* Set `WA_OCTOPUS_TARIFF=E-1R-COSY-22-12-08-A` (your tariff code) instead; rates are public.
3. Deploy, then check: `docker compose exec worker python -m app.energy.sync` prints the tariff and the number of rates stored.

`WA_ENERGY_POLL_ENABLED=false` switches the module off. With neither account nor tariff set it does nothing.

## API

| Endpoint | Returns |
|---|---|
| `GET /v1/energy/now?hours=3` | current rate and band, next change (time, minutes, rate), next cheap and peak windows, cheapest block of `hours` in the next 24 h, 24 h average |
| `GET /v1/energy/rates?hours=48` | each upcoming slot with its band |
| `GET /v1/energy/windows?hours=3` | the cheapest block, and every banded window |

## Home Assistant

A device **Energy** with: Electricity rate (p/kWh), Rate band, Cheap rate now (`on`/`off`), Next rate, Next
rate band, Next rate change, Minutes to rate change, Next cheap window starts / ends, Next peak window starts,
Cheapest block starts, Average rate next 24h, and Upcoming rates (the next slots as an attribute).

Example automation idea: when `sensor.wa_energy_cheap_now` turns `on` and the battery is below target, start
grid charging; when `sensor.wa_energy_rate_band` is `peak`, run from battery.

## Asking by voice

The assistant has an `electricity` tool (`POST /v1/ask/electricity?format=text`, also over MCP and Siri Shortcuts): "Electricity is cheap now at 14.3p, until 16:00, then peak at 43.7p. ..." with the next cheap and peak times, the cheapest block and any Saving Session.

## Page

`/energy` in the browser (token as on the other pages): the rate and band now, a 48-hour bar chart coloured by
band with a "now" line, the next cheap and peak windows, the cheapest block of 1 to 6 hours, Octopus sessions,
and a table of every upcoming rate. It refreshes each minute.

## Sessions (phase 2)

Every 30 minutes WA also reads:

* **Saving Sessions** from Octopus's GraphQL API (needs the key and account number): the announced events, and
  whether you have joined each. WA never joins for you; do that in the Octopus app.

* **Free Electricity** (Power Ups, Weekend Happy Hours) from the same Saving Sessions feed, where Octopus lists them
  by type (`WEEKEND_HAPPY_HOUR`, `TURN_UP`) and says which hours you joined. You sign up hour by hour, so your joined
  hours come first. Home Assistant: `sensor.wa_energy_free_electricity_now` is `on` only during an hour you joined
  (attribute `announced_now` says whether any is on); `sensor.wa_energy_free_electricity_next_start` is your next
  joined hour, else the next announced one, with attributes `end`, `joined`, `type` and `upcoming`.

  To act on it (an inverter, a car charger), trigger on the timestamp and check `joined`:

  ```yaml
  triggers:
    - trigger: time
      at: sensor.wa_energy_free_electricity_next_start
      id: start
    - trigger: state
      entity_id: sensor.wa_energy_free_electricity_now
      from: "on"
      to: "off"
      id: stop
  conditions:
    - condition: template
      value_template: "{{ trigger.id == 'stop' or state_attr('sensor.wa_energy_free_electricity_next_start', 'joined') }}"
  ```

The queries follow the BottlecapDave Home Assistant integration and are not an official Octopus contract, so a
change at Octopus could break them. The rates keep working if the sessions fail. To see exactly what Octopus
returns for your account (account number and tokens are blanked out):

```
docker compose exec worker python -m app.energy.probe
```

API: `GET /v1/energy/sessions` (also under `sessions` in `/v1/energy/now`). Home Assistant sensors: Saving session
now (`on`/`off`, with a `joined` attribute), Next saving session starts, and Next joined saving session starts. Migration 0024 (`energy_session`).

Example: when `sensor.wa_energy_saving_session_now` is `on` and its `joined` attribute is true, run the house from
the battery.

## Running the battery from Home Assistant

The energy plan (`wa_energy_plan.yaml`, on the Home Assistant page, /ha) is a Home Assistant package for a
Deye/Sunsynk-style inverter with six daily time slots. It works with any Octopus tariff:

* World Assistant works out the six slots from your real rates (`GET /v1/energy/schedule`, and
  `sensor.wa_energy_charge_schedule` in Home Assistant): a slot starts at each cheap window (grid charge on) and at its
  end (off), up to three windows a day, the cheapest kept. Cosy comes out as 00:00 04:00 07:00 13:00 16:00 22:00 with
  grid charge on 04:00, 13:00 and 22:00; Go as one window overnight; Agile as the cheapest windows of the coming day.
  A rate not published yet is taken from the same time a day (or a week) earlier.
* Each charging slot is charged to the battery floor plus what the house uses until the next cheap window (night
  00:00-07:00, day 07:00-16:00 and evening 16:00-24:00 loads, with a margin), less the solar Forecast.Solar expects in
  that time, never above the battery health max (80% by default; above it the plan stops and suggests raising it).
  The other slots are set to the floor (20% by default), which is how low the inverter lets the battery discharge.
* A window can only add the inverter's grid charge rate times its hours (read from the inverter; a setting if it
  cannot be). When a window cannot put back what the next stretch needs, the window before it charges higher.
* A joined Saving Session raises the target of the slot that covers it; one inside a charging window is warned
  about. In a joined Free Electricity hour the slot it falls in charges from the grid (to the health max), and is put
  back when the hour ends; a charging slot before a joined free hour only covers the time up to it.
* With "WA energy plan: set slot times from the tariff" on, the plan also writes the slot start times and grid-charge
  switches; off, it only applies when they already match ("WA inverter slot check").
* Outcomes go to the logbook too ("Outcomes" on the dashboard): at the end of each charging window, the target and
  what the battery reached; at the start of the next, the lowest it went and what was bought at dearer rates; and a
  daily summary at midnight. They show what happened, applied or not, so they are the record to tune against.
* It starts as a dry run that only writes to the logbook; switch on "WA energy plan: apply to inverter" when the
  numbers look right. Nothing in the package switches the grid off: any shortfall is taken from the grid by the
  inverter itself, so the cost of a shortfall is money, never a loss of supply.

### Car charging plan

`wa_ev_plan.yaml` (on /ha) charges the car through an OCPP charger in Home Assistant's OCPP integration (tested on a
Sync EV EVCP-7KW-S-1PH pointed at `ws://<Home Assistant>:9000/`), with the car's charge level from OVMS or any other
integration. The car charges only while the rate is cheap (World Assistant's live rate band, which follows the tariff
as it is, or one of the energy plan's charging windows), in a Free Electricity hour you joined, or, with "always charge
when low" on, below the level you set; it stops at "WA car: charge to" (80%). Set your charger's and car's entities in
"Car settings" on the Home charging dashboard (empty fields use the defaults named there). Turn the charger's own
schedule off (in its app, before moving it to Home Assistant). It starts as a dry run ("WA car: control the charger").

### Home charging dashboard

The Home charging dashboard (`home-charging.yaml`, on /ha) is a Home Assistant dashboard for all of this, with built-in cards only:
battery, solar, house and grid now; the price now and next; free hours announced (joined or not) and Saving
Sessions; Forecast.Solar today, still to come and tomorrow; the three charge targets next to what the inverter is
set to; the apply switch and slot check; a logbook of every decision the plan made; and the tuning sliders. Add a
dashboard from scratch, open its raw configuration editor, and paste the file in, or copy it over Samba from /ha.

## Notes

* The rates and bands are not verified against a live account by the developers; the first scheduled run is the test.
* Times are stored in UTC and published as ISO 8601 timestamps.
* Prices include VAT.

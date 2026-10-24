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

`docs/ha/wa_energy_plan.yaml` is a Home Assistant package for a Deye/Sunsynk inverter that already has Cosy-shaped
time slots (00:00 04:00 07:00 13:00 16:00 22:00, grid charge on for slots 2, 4 and 6). It changes only the target
charge level of the three charging slots, from expected use and tomorrow's solar forecast (Forecast.Solar). It
starts as a dry run that only writes to the logbook; switch on "WA energy plan: apply to inverter" when the numbers
look right. On Cosy the cheap windows are fixed and already in the slots, so WA is not needed for those. WA's Saving Session
sensors are used to raise the 13:00 target when you have joined a session between 16:00 and 22:00, to warn if the
battery runs low during a joined session, and to flag a joined session outside 16:00-22:00 that the plan does not
handle. In a **Free Electricity hour you have joined** it sets that hour's slot to charge from the grid to 100%
(so the house runs from the grid and the battery fills, for free) and puts the slot back when the hour ends; the 04:00
target only covers the morning up to a joined free hour. Before applying, it checks the inverter's slot start times and grid-charge switches (sensor "WA inverter slot check"); if someone has changed them it only logs what differs. Nothing in the package switches the grid off or limits what the inverter can draw: any shortfall is taken
from the grid by the inverter itself, so the cost of a shortfall is a smaller reward, not a loss of supply.

### Home charging dashboard

`docs/ha/wa_home_charging_dashboard.yaml` is a Home Assistant dashboard for all of this, with built-in cards only:
battery, solar, house and grid now; the price now and next; free hours announced (joined or not) and Saving
Sessions; Forecast.Solar today, still to come and tomorrow; the three charge targets next to what the inverter is
set to; the apply switch and slot check; a logbook of every decision the plan made; and the tuning sliders. Add a
dashboard from scratch, open its raw configuration editor, and paste the file in.

## Notes

* The rates and bands are not verified against a live account by the developers; the first scheduled run is the test.
* Times are stored in UTC and published as ISO 8601 timestamps.
* Prices include VAT.

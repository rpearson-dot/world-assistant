# Energy supply

World Assistant reports what electricity costs now and later. **Home Assistant acts on it.** Nothing in
WA talks to an inverter or battery: WA is the outside world, HA owns the house. A Sunsynk inverter (for
example through the kellerza add-on) is driven from HA automations that read the sensors below.

## What it does (phase 1: rates)

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

## Not yet (phase 2)

Octopus Saving Sessions and Free Electricity sessions (the "calendar") come from Octopus's GraphQL API and
need your account; they will be added as further sensors once the response can be inspected with a probe
command against your account.

## Notes

* The rates and bands are not verified against a live account by the developers; the first scheduled run is the test.
* Times are stored in UTC and published as ISO 8601 timestamps.
* Prices include VAT.

# Tariff comparison

Energy → **Tariffs** (`/tariffs`). Turn it on in Settings → Modules. It needs the Octopus module's meter readings,
which need your account number and API key.

## What it does

It takes your real half-hourly readings from the meter (import, and export where you have an export meter) and
prices them at **today's rates** for every tariff, half-hour by half-hour. It never uses past price levels, only your
past use.

- **Octopus's tariffs** for your region come from its public products API. Every tariff a household can move to now
  is included (not business, prepay or restricted ones), plus your own.
- **Other suppliers' tariffs** you add by hand from a quote, under "Tariffs from other suppliers": each band's unit
  rate, the standing charge, and the export rate if it comes with one. A tariff with an export is only paired with its
  own export.
- **Agile and Tracker** change every day, so they are priced at the average of each half-hour over the last 30 days.

## Two costs for each tariff

- **As used:** your half-hours exactly as they were. These already follow your current tariff's cheap hours, so this
  flatters your current tariff.
- **With battery:** a half-hour-by-half-hour simulation of your house battery filling in *every* cheap window of that
  tariff (as many as it has each day, at the charge rate you set) and covering use outside them until it is empty, with
  10% lost going in and out. Set the battery's usable kWh and charge rate in Settings on the page (32 kWh and 5 kW to
  start).

Each tariff also shows its cheap hours: how many windows, how long, and the longest gap between them, which is what
the battery has to bridge. A tariff's cheap half-hours are those within a quarter of the way from its lowest rate to
its highest.

The ranking and the suggestion use the lower of the two costs, with the best export that can go with each import.

## Which readings count

Readings count from the date you set ("Count readings from") and never from more than a year back. Set it to when the
house last changed (solar fitted, gas removed). With less than a year of readings, the yearly figures are scaled up
from the days there are, and they lean towards the season those days were in. The page says when this is the case.

The meter readings go back a year the first time they are fetched. Installs that fetched only 90 days before 0.53.0
fill in the rest the next morning.

## What it cannot count

- Intelligent Octopus Go's extra cheap hours when it charges the car.
- Exit fees on your current contract. The fees of tariffs you would move to are shown where Octopus publishes them.
- Discharging the battery to the grid for export.
- Whether you meet a tariff's conditions. These are shown under each name: an electric car for Go, a heat pump for
  Cosy, solar and a battery for Flux.

This is a comparison, not advice: check a tariff's terms before you switch.

It is worked out each day at 11:13, after the morning's meter readings arrive, or when you press "Work it out again".

## API

| | |
|---|---|
| `GET /v1/tariffs` | the last comparison and the settings |
| `POST /v1/tariffs/compare` | work it out again now |
| `PUT /v1/tariffs/settings` | the date readings count from, the battery, the region, other suppliers' tariffs |

Assistant tool: `best_tariff`.

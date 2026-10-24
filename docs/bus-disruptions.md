# Bus disruptions (BODS)

Local transport authorities can publish bus, tram and ferry disruptions (diversions, closures, stops out of use) to
the Bus Open Data Service as SIRI-SX. World Assistant reads the national file every 10 minutes with your
`bods_api_key` and keeps the ones that are current and within 25 km of a saved place.

**Only some authorities publish.** In October 2026: Greater Manchester, West of England, South and West Yorkshire,
Merseyside, Cornwall, Devon, Hampshire, North Lincolnshire, Telford & Wrekin, Bedford/Luton and Cheshire East.
Not London (TfL's road disruptions cover that) and not the Midlands. See who publishes now, and what is near you:

    docker compose exec worker python -m app.integrations.bods.disruptions_probe [lat lon] [km]

## Switching it on

It is off by default. In `.env`:

    WA_BUS_DISRUPTIONS_POLL_ENABLED=true
    WA_BUS_DISRUPTIONS_KM=25        # optional

then `bash scripts/deploy.sh`. It also needs the Bus Open Data module (the same key) and can be switched off on the
Modules page like any other.

## What is kept

- current: not closed, not ended; one with no end that started more than 90 days ago is treated as left over and
  dropped (some publishers never close them);
- near: one of its stops (placed with the national stop register) or a place it names is within the distance of a
  saved place. Ones that name only a road and lines cannot be placed and are not kept.

`GET /v1/bus-disruptions` lists them, closest first (`place`, `line`, `within_km`, `limit`).

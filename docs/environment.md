# Air and water

Air quality near each of your places, and river levels, rainfall and tide gauges near home, read live from Defra
and the Environment Agency. No key is needed. Rivers, rain and tides cover England only (the Environment Agency's
area); air quality covers the UK.

| What | Where from | What you get |
| --- | --- | --- |
| Air quality | [Defra UK-AIR](https://uk-air.defra.gov.uk/data/about_sos) monitoring stations | Defra's **Daily Air Quality Index** (1-3 low, 4-6 moderate, 7-9 high, 10 very high) and the readings it comes from: nitrogen dioxide (hourly), ozone (8-hour mean), PM2.5 and PM10 (24-hour means), each from the nearest station measuring it in the last six hours |
| River level | Environment Agency river gauges | the level now, whether that is low, normal or high **for that gauge** (its usual range), and rising, falling or steady over three hours |
| Rain | Environment Agency rain gauges | millimetres in the last hour and the last 24 hours |
| Storm overflows | Thames Water's monitors, via Water UK's [National Storm Overflow Hub](https://www.water.org.uk/NSOH) (CC BY 4.0) | each overflow within 3 km of home and on the rivers you watch (within 15 km): discharging now and for how long, discharged in the last 48 hours, or monitor offline |
| Tide | Environment Agency tide gauges | the measured level and whether it is rising or falling. It is not a forecast: high and low water times are not in this data |

The index is worked out here from the readings, as Defra defines it, so it can differ slightly from Defra's own
published figure for the day. The nearest station may be a roadside one, which reads higher than a back garden;
its name and distance are given.

## Storm overflows

A storm overflow lets sewage-diluted rainwater into a river when the sewers are full. Each has a monitor that says
when it is spilling (not how much, or how dirty), and Thames Water publishes these within minutes. You get the ones
near home, and any on the rivers you choose to watch, up to 15 km away, which catches spills upstream:

    curl -X PUT -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \
         -d '{"rivers": ["Hogsmill", "Thames"]}' http://<nas>:8010/v1/environment/overflows/rivers

or ask the assistant to "watch the Hogsmill and the Thames" (`watch_rivers`). Any part of the name matches, so
"Hogsmill" finds "Hogsmill River". An offline monitor could be spilling unseen, so those are counted too.

To be told when one starts, make a Home Assistant automation on `sensor.storm_overflows_<home>` going above 0; the
attributes say which, on which river, how far away and since when.

Only Thames Water's feed is read, since home is in its area. The other companies publish the same way, and adding
one is a line in `OVERFLOW_FEEDS` (app/integrations/environment/clients.py).

## Where to see it

- **Home Assistant:** an "Air and water" device with `air_quality_<place>` for each place, and `river_level_<home>`,
  `rain_24h_<home>`, `storm_overflows_<home>` (how many are spilling now) and `tide_<place>` (for places within 10 km of a tide gauge). The details are in each sensor's
  attributes.
- **Assistant:** the `air_quality`, `river_levels` (with the rain), `storm_overflows`, `watch_rivers` and `tides` tools.
- **API:** `/v1/environment/air`, `/rivers`, `/rain`, `/tides`, `/overflows` (each takes `place` and `km`),
  `/overflows/rivers` (GET or PUT) and `/summary`.

Answers are kept for ten minutes; the sources update every 15 minutes (rivers, rain, tides) or hourly (air).

## Checking it works

Neither source needs setting up, but both are someone else's service. To see exactly what each sends near home (or
a saved place):

    docker compose exec api python -m app.environment [place]

Each part says what it found, or why it failed. Because these are read live, the deploy check leaves them out, so a
slow day at Defra cannot hold up an update.

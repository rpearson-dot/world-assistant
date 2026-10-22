# Flood warnings

The Environment Agency publishes every current flood alert and warning for England as open data (no
key, Open Government Licence). World Assistant fetches the list every **15 minutes**, which is how
often the EA updates it, and keeps it in Postgres.

> This uses Environment Agency flood and river level data from the real-time data API (Beta).

The service has no availability guarantee and is **not a substitute for the official warnings**
(gov.uk/check-flooding, or the Floodline). Treat this as a convenience, and sign up for the EA's own
warnings if flooding matters where you live.

## What it covers

| Level | Name | Meaning |
| --- | --- | --- |
| 1 | Severe flood warning | severe flooding, danger to life |
| 2 | Flood warning | flooding expected, immediate action required |
| 3 | Flood alert | flooding possible, be prepared |
| 4 | No longer in force | kept by the EA for about a day after a warning ends |

Level 4 is stored (it explains a warning that has just gone) but is never drawn or counted as live.
Scotland, Wales and Northern Ireland publish their own warnings elsewhere; none of that is here.

## How it works

A warning is small and changes often; the area it applies to is a polygon that never changes. They
are kept apart (`flood_warning`, `flood_area`). The first time a warning needs an area, its
boundary is fetched and stored, up to 25 a run, so a big storm fills in over a few runs rather than
in one burst. Until a boundary is held, that warning cannot be tested against a place, and
`/v1/floods/at` says how many are waiting (`boundaries_missing`).

Anything stored that is no longer in the EA's list is deleted, but only after a well-formed answer.
A failed or garbled fetch changes nothing; an empty list is a real answer (no warnings) and clears
the table.

## Asking

| Endpoint | Answers |
| --- | --- |
| `GET /v1/floods?max_level=3` | every live warning, most serious first (`max_level=4` includes ended ones) |
| `GET /v1/floods/at?place=home` | live warnings whose area contains a place (default: home) |
| `GET /v1/floods/at?lat=&lon=&within_km=` | the same for a point; `within_km` widens "inside" to "within" |
| the `floods` map layer | each warned area, coloured by severity, on by default |
| `GET /v1/graph/disruption?place=` | with the graph on: warnings over the place and over its trips' corridors |

By hand: `docker compose exec worker python -m app.integrations.flood.sync`.
`WA_FLOOD_POLL_ENABLED=false` switches the job off.

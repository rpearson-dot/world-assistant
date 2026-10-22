# TfL road disruptions

London's roadworks, closures, collisions and incidents from Transport for London's Unified API
(`GET /Road/all/Disruption`), fetched every 15 minutes and kept with where each one is.

National Highways covers motorways and major A-roads only. Most of London's roads are TfL's or the
boroughs', so this is the other half of "what is on my way".

## What you get

- `GET /v1/roads/tfl` lists current and scheduled disruptions, unplanned first, worst first.
  Options: `scope=planned|unplanned`, `closures_only=true`, `limit`.
- `GET /v1/roads/tfl/at` lists those within `radius_m` (default 500) of a point or saved place, nearest first.

## How it works

- One call returns the whole list, so a stored disruption missing from a good response has ended and is removed.
  A response that is not a list never removes anything, and an empty list over a populated table is treated as a
  failed fetch.
- A disruption is **unplanned** when its category or sub-category names an incident (collision, breakdown, burst
  main, flooding, hazard and so on), otherwise **planned**. TfL's categories drift, so the match is on words.
- The position comes from the affected streets' lines when TfL gives them, otherwise its GeoJSON, otherwise its
  point. One with no usable position is still listed but cannot be matched to a route.
- Times without an offset are read as London time (`WA_TFL_NAIVE_TZ`).

## Setting up

Works without a key at a low rate. Put your TfL app key in the `tfl_app_key` secret for the higher limit.
`WA_TFLROADS_POLL_ENABLED=false` turns the poll off.

The TfL response shape is only partly documented, so after the first deploy run the probe and check that most
disruptions have a position:

    docker compose exec worker python -m app.integrations.tflroads.probe
    docker compose exec worker python -m app.integrations.tflroads.sync

## Limits

- No direction of travel: a closure on one carriageway shows for a route using the other.
- It says what is *reported*, not how slow traffic is. There is no live congestion.

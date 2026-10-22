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
- Where it is, most exact first: the affected streets' lines when TfL gives them (about one in five, October 2026);
  otherwise the **affected area** in its `geometry` field (a MultiPolygon, one shape per stretch of road);
  otherwise its `geography` point. So long works match along their length, not only at the
  one point TfL marks. One with no usable position is still listed but cannot be matched to a route.
  `roadDisruptionLines` and `roadDisruptionImpactAreas` are present but were empty in every item seen (October 2026), so they are not read.
- Times without an offset are read as London time (`WA_TFL_NAIVE_TZ`).

## Setting up

Works without a key at a low rate. Put your TfL app key in the `tfl_app_key` secret for the higher limit.
`WA_TFLROADS_POLL_ENABLED=false` turns the poll off.

The TfL response shape is only partly documented. The probe shows what came back and what each disruption is
stored as (lines, areas or points):

    docker compose exec worker python -m app.integrations.tflroads.probe
    docker compose exec worker python -m app.integrations.tflroads.sync

## Limits

- No direction of travel: a closure on one carriageway shows for a route using the other.
- It says what is *reported*, not how slow traffic is. There is no live congestion.

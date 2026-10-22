# Trip conditions: what is on the way

For a trip with a route, "conditions" says what is reported on that route **now** and what is **planned**.

It combines three sources, each read from what the pollers have already stored:

| Source | Covers | How it is matched to the route |
|--------|--------|--------------------------------|
| National Highways | Motorways and major A-roads: closures, speed limits, works | Events already tagged to the trip's corridor (road number, then distance to the route) |
| TfL road disruptions | London's roads: works, closures, collisions, incidents ([tfl-roads.md](tfl-roads.md)) | Within `buffer_m` (default 75 m) of the route line |
| Environment Agency | Flood alerts and warnings, England ([floods.md](floods.md)) | The warned area crosses the route line |

Outside London and off the strategic network nothing reports local roads, so "clear" means *nothing reported*,
not that the road is empty. There is no live traffic and no direction of travel.

## Using it

    GET /v1/trips/{id}/conditions                      # now, and planned in the next 7 days
    GET /v1/trips/{id}/conditions?when=2026-10-26T07:30:00+00:00
    GET /v1/trips/{id}/conditions?days=14&buffer_m=150
    GET /v1/conditions                                 # every enabled trip with a route

- **`when`** is when you set off. "Now" is what affects a journey that starts then and lasts as long as the route
  takes; "planned" is what starts after that, up to `days` later. So `when` tomorrow morning answers "will the works
  that start tonight be in my way at 07:30?".
- A trip with no route yet answers 409; build it first (Places page, or `POST /v1/trips/{id}/route`).
- The answer has `now.status` (`clear`, `minor`, `disrupted`, `severe`), the items behind it, and a `summary` written
  to be read aloud.

Status is the worst item: a National Highways closure is severe; a speed limit is minor; TfL's severity maps to
minor, disrupted or severe, one step up when the road is closed; a flood alert is minor, a warning disrupted, a
severe warning severe.

Flood warnings are shown only for a departure in the next 12 hours (they describe now, not next week), and
incidents for a later `when` are whatever is reported now, so the response says so in `caveats`.

## Where it shows up

- **Places page**: each trip with a route shows its status, with a Conditions button for the detail.
- **Home Assistant**: a **Trips** device with, for each routed trip, `<trip>: conditions` (clear / minor / disrupted /
  severe, with the summary and items as attributes) and `<trip>: planned works` (a count, with the next start).
  Use the status in an automation to warn before you leave.
- **Assistant** (`/v1/ask/road_conditions`, Siri, MCP): "is there anything on my way to work?" Leave the trip out for all.

## Know before relying on it

- The match is geometric. A flood area that crosses the route may not put water on the road, and a TfL closure
  on a parallel street within 75 m can show. Raise or lower `buffer_m` to taste.
- The route is Valhalla's best road line, simplified to about 30 m. Your actual road may differ.
- TfL's response shape was not verifiable when this was written: run `python -m app.integrations.tflroads.probe`
  and check most disruptions have a position.

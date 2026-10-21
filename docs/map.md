# Map layers

Every dataset World Assistant holds with coordinates, served as GeoJSON so one map can draw them
together, a layer at a time. This is the data side only; nothing draws it yet.

## What there is

| Layer | Shape | From | Carries |
| --- | --- | --- | --- |
| `places` | points | the places you saved | name, kind, postcode, whether Home Assistant made it |
| `locations` | points | Home Assistant app locations | name, radius, when last seen |
| `roads` | lines | corridors built from your trips | name, road numbers, corridor width |
| `road_events` | lines and points | National Highways, still open | closure or speed limit, road, cause, times |
| `signs` | points | motorway signs | road, direction, what it is showing |
| `fuel` | points | Fuel Finder stations | brand, name, the price of one fuel and when it was set |
| `pollen` | points | the allergy-risk lattice | today's risk label and level 0–4 |

Bus stops are not a layer: the tracker stores a stop's id and name but not its position. Adding them
means asking TfL for each stop's coordinates once and storing them.

## Asking for it

    GET /v1/map/layers

What can be drawn and how many features each layer has.

    GET /v1/map/geojson?layers=places,fuel,roads&radius_km=10

`layers` is a comma-separated list. The centre is the default place unless you give `place=<id>` or
`lat=` and `lon=`; `everywhere=true` drops the centre and returns the lot. `fuel=` chooses which
fuel's price rides along with the stations (`E10` by default).

The answer is `{"centre": ..., "layers": {"<id>": <FeatureCollection>}}`. Coordinates are
`[longitude, latitude]`, as GeoJSON requires, though the rest of the project stores `(lat, lon)`.

Point features outside the radius are left out. A line is returned whole if any part of it falls
inside, so a closure is never drawn half-finished.

A token is needed, like every other `/v1` route. The layers include where people are, so treat the
answer as private.

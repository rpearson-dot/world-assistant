# Map layers

Every dataset World Assistant holds with coordinates, on one map, a layer at a time. The `/map`
page draws them; `/v1/map/geojson` serves them to anything else.

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

## The page

`/map` asks for your token, then draws the layers over street tiles. Tick a layer to show it; the
number beside each name is how many features came back. Click anything for its detail. Places,
corridors, closures and fuel are on at first; people, signs and pollen are off.

Fuel stations are coloured against the cheapest one on screen: green within 2p of it, amber within
6p, red beyond. Pollen points take the allergy-risk colours of the pollen map.

The centre is whichever place you pick, out to the distance you choose, or **Everywhere** for the
lot with no centre. Points are drawn on a canvas rather than one shape each, so Everywhere stays
quick even with thousands of stations.

## What the browser fetches

Leaflet is served from this app (`app/static/vendor/leaflet`), not from a CDN, so no page loads a
third-party script and the version cannot change underneath you. `VERSION.txt` beside it records
the version and the hash it was taken with; upgrading means repeating that deliberately.

The map tiles are the one thing your browser fetches from elsewhere, and whoever serves them can
see which parts of the map get looked at. By default they come from OpenStreetMap:

| Setting | Default | What it does |
| --- | --- | --- |
| `WA_MAP_TILE_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | where tiles come from; point it at your own server to keep it in the house |
| `WA_MAP_TILE_ATTRIBUTION` | `© OpenStreetMap contributors` | shown on the map; the licence requires crediting whoever made the tiles |
| `WA_MAP_MAX_ZOOM` | `19` | how far in the tiles go |

In dark mode the tiles are inverted in the browser so the map does not glare; your data keeps its
own colours.

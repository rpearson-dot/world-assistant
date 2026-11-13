# The module SDK

A **module** is one area of World Assistant: fuel, the bus tracker, the bike garage. Every module lives in its own
folder, `app/modules/<id>/`, and talks to the rest only through a small, written interface: this page. The
interface is kept in code in `app/modules/sdk.py`, and `tests/test_module_boundaries.py` holds every module to it.
To add a module, read [contributing a module](contributing-a-module.md) first.

## The rules

1. **The core never imports a module.** When the core needs something a module has (an assistant tool, a map layer,
   a key test), the module **provides** it at an extension point, and the core asks the registry
   (`registry.provided(point)`, `registry.provider(point)`). A missing or switched-off module simply provides
   nothing.
2. **A module imports only:**
   - the core's published interface (below);
   - its own folder;
   - the modules it declares: in `needs` (it cannot work without them, and they are switched on with it) or `uses`
     (it uses them when they are there, and must cope when they are not).
3. **Everything a module adds is declared** in its `Module(...)` (`app/modules/builtin.py`): routes, jobs, keys,
   tools, what it needs and uses, and what it provides. A module never edits a core list.

## The core's published interface

| Core module | What a module uses it for |
|---|---|
| `app.config` | settings (`get_settings`) and their defaults |
| `app.auth` | `require_token`, the dependency every `/v1` router uses |
| `app.places` | saved places: `place_or_default`, the default place |
| `app.geo` | postcode lookup (postcodes.io, or a module's local table) |
| `app.geoquery` | distance SQL for PostGIS (`POINT`, `within_sql`, `distance_km`) and `haversine_km` |
| `app.corridor` | a route's corridor (centreline and buffer) |
| `app.ratelimit` | the shared rate limiter for outside services |
| `app.jobs` | recording a worker job's run, so Settings shows when it last worked |
| `app.secretstore`, `app.keys` | reading a secret, and the Keys page's view of one |
| `app.notify`, `app.notify_jobs` | phone notifications: `send`, `sent`, `settings`; `send_many` and `page_link` |
| `app.ha` | Home Assistant shapes (`Device`, `Sensor`, `Image`) |
| `app.assistant_base` | `Tool` and its helpers, for assistant tools |
| `app.pagestyle`, `app.themes`, `app.whereami` | the page shell (`themed`), theme tokens, the shared place picker |
| `app.map_layers` | `near_rows` and `point_feature`, for a map layer of the module's own |
| `app.setup_checklist` | the Setup page's states, for a setup check |
| `app.bankholidays` | UK bank holidays |
| `app.integrations` | shared clients: what3words, Ordnance Survey GeoPackage reading |
| `app.logging`, `app.version` | log formatting, the running version |
| `app.modules.model`, `app.modules.registry` | `Module` and `Job` to declare a module; what other modules provide |

Anything else in the core is its own business and may change between releases.

## Extension points

A module plugs in with `provides={"<point>": "app.modules.<id>.<file>:<name>"}`.

| Point | What the module gives |
|---|---|
| `assistant.tools` | a list of `Tool`: its assistant tools (MCP and Siri) |
| `key.test` | `async (settings) -> (ok, message)`: a live check that its key works |
| `setup.check` | `async (settings) -> dict`: a line on the Setup page's "Your setup" list |
| `shutdown` | `async () -> None`: close its own connections when the api or worker stops |
| `ha.energy_device`, `ha.energy_retired` | the Home Assistant energy device, and the sensors it no longer publishes |
| `ha.pollen_map` | `(points) -> svg`: the pollen map image for Home Assistant |
| `map.ev`, `map.live_buses` | the map's EV charger and live bus layers |
| `fuel.canonical` | `(text) -> fuel code or None`, for the map's fuel names |
| `naptan.station_types` | the stop types that count as stations on the national map |
| `bus.nearest_slot` | a bus tracker's timetable slot, for Home Assistant |
| `postcodes.local` | `async (engine, postcode) -> Located or None`: a local postcode table |
| `corridors.save`, `corridors.rematch` | keep a routed trip's corridor, and match stored events to every corridor |
| `events.search` | `async (request, name, lat, lon, ...) -> events`: what is on near a point |

A new point is added to `POINTS` in `app/modules/sdk.py`, with the core code that asks for it, in the same change.

## A module's folder

```
app/modules/<id>/
  README.md        written by scripts/module-readmes.py: what it does, needs, uses, provides; its files
  <id>.py          its API (an APIRouter under /v1/<id>), and the rules
  page.py          its pages, if it has any
  tools.py         its assistant tools (provides "assistant.tools")
  notify.py        its phone notifications
  sources/<name>/  each outside service it reads: client, normalise, store, sync, probe
```

## New installs

A new install starts from the essentials: only the always-on core. The setup wizard's second step asks which
areas to add, and Settings → Modules switches any module on or off later. An install that was already in use
keeps every module it had on.

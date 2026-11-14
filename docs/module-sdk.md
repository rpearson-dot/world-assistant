# The module SDK

A **module** is one area of World Assistant: fuel, the bus tracker, the bike garage. Every module lives in its own
folder, `app/modules/<id>/`, and talks to the rest only through a small, written interface: this page. The
interface is kept in code in `app/modules/sdk.py`, and `tests/test_module_boundaries.py` holds every module to it.
To add a module, read [contributing a module](contributing-a-module.md) first.

## Versions

The SDK has a version, `SDK_VERSION` in `app/modules/sdk.py`. A module says which version it was written for
(`Module(sdk=2)`); a module written for a newer SDK than the running World Assistant offers is not loaded, and the
log says to update World Assistant. Additions raise the version; nothing is taken away without a version that says so.

| Version | Release | What it added |
|---|---|---|
| 1 | 0.52.0 | the first: the rules, the published interface and the extension points below |
| 2 | 0.53.0 | the `startup` extension point; `Module.tabs` (its pages as tabs of a menu section); `Module.occasional` (jobs that run only at times); `app.localnet`, `app.mqtt`, `app.mqtt_config`, `app.ha_discover` and `app.modules.gate` in the published interface |
| 3 | 0.54.0 | `Module.role`, `reads`, `gives` and `controls` (what a module does with data, shown on the Setup page and in its README); `app.ha_stats` (Home Assistant's states and long-term statistics) and `app.ha_smb.read_config_file` (a file from Home Assistant's config folder) in the published interface |

## What a module does with data

Every module says what it does with data, on its declaration:

- `role`: **source** (fetches from outside), **processor** (works from what other modules and Home Assistant hold)
  or **output** (gets things to you). Most do a little of each; the role is the main one.
- `reads`: outside services, devices on the home network and Home Assistant. The modules it uses come from `needs`
  and `uses` and are not repeated.
- `gives`: Home Assistant entities, notifications and anything else that leaves World Assistant. Its pages and
  assistant tools come from `tabs`, `prefixes` and `tools`.
- `controls`: what it changes in the real world (a charger, an air conditioner). Empty for most.

The Setup page shows them under each module, and its README lists them. `tests/test_module_flows.py` checks them
against the code: a module that sends notifications, publishes to Home Assistant, reads Home Assistant or commands a
device must say so. The built-in modules' entries are one table in `app/modules/flows.py`.

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
| `app.localnet` | `private_peer`, `proxied`: who connected, for an endpoint only the home network may use |
| `app.mqtt`, `app.mqtt_config` | publishing to the MQTT broker Home Assistant reads; the broker's settings and a client, to listen |
| `app.ha_discover` | `ha_api` (Home Assistant's address and token, when set) and `live_states`, to read its states |
| `app.modules.gate` | `is_enabled`: whether a module is switched on (a websocket, which the gate does not cover) |

Anything else in the core is its own business and may change between releases.

## Extension points

A module plugs in with `provides={"<point>": "app.modules.<id>.<file>:<name>"}`.

| Point | What the module gives |
|---|---|
| `assistant.tools` | a list of `Tool`: its assistant tools (MCP and Siri) |
| `key.test` | `async (settings) -> (ok, message)`: a live check that its key works |
| `setup.check` | `async (settings) -> dict`: a line on the Setup page's "Your setup" list |
| `startup` | `async (app) -> None`: start its long-lived work in the api once it is up (a charger's connection, an MQTT listener). Two api processes run during an update, so take a Redis lease if only one may run it |
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

## More of what a module declares

- **Menu tabs** (`tabs=(("bus", "buslog", "/buslog", "Route log"),)`): its pages as tabs of a menu section. The
  section is a menu page's id; that page stays the first tab.
- **Occasional jobs** (`occasional=("buslog",)`): of its `jobs`, the ones that run only at times (a morning window, a
  season). World Assistant watches every other job and tells you when one goes quiet for longer than it ever has;
  these it leaves alone.
- **Keys:** never a new secret file in `docker-compose.yml` (an update cannot create one, and a missing secret file
  stops the containers starting). A module's keys are saved on the Keys page; `tests/test_secrets_compose.py`
  enforces it.

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

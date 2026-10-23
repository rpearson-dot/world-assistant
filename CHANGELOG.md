# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Council and region boundaries (OS Boundary-Line)**: regions, counties and districts or boroughs, loaded on first start, simplified so they stay small, and reloaded twice a year. `WA_BOUNDARY_KINDS` adds wards, parishes and constituencies. Shown on the Setup list ([docs/boundaries.md](docs/boundaries.md)). Migration 0027.
- **Street numbers (OS Open USRN)**: every street's USRN and line, the number roadworks are reported against. A million and more streets, so off unless `WA_USRN_POLL_ENABLED=true`.
- **Knowledge graph: areas, road disruptions and what's on**: `Area` nodes (the regions, counties and districts your places, stops, events and disruptions sit in, with `IN` links up the hierarchy; only areas something is in), `Disruption` nodes (live TfL road works and incidents, `AFFECTS` the corridors they lie along, `NEAR` places) and `LocalEvent` nodes (Skiddle and Ticketmaster, `NEAR` and `IN`). New ready-made questions for each.
- **What's on is now stored** (`local_event`, migration 0028): every six hours the next two weeks around your saved places are copied from Skiddle and Ticketmaster (`WA_EVENTS_SYNC_*`), so the graph can relate them. Does nothing without a key.
- **Graph page > Customise the view**: choose which kinds of thing and which links are shown, pick each kind's colour, size nodes by how connected they are, show names always, when zoomed in or never, hide link names, and set node size and spread. Remembered in the browser; Reset puts it back.
- Both read an OS GeoPackage with no new dependency, and `--list` shows what a download holds. Not yet run against the live OS downloads.

## [0.15.0] - 2026-10-22

### Added
- **Settings > MQTT**: the broker World Assistant publishes to, with server, port, user, password and an **Encrypted (TLS)** switch (the port follows it between 1883 and 8883), **Test connection** with failures explained in words, Save (wins over `.env`, used from the next publish, no restart) and **Use .env**. The Setup list and the Home Assistant page show the broker in use.
- **Copy straight into Home Assistant** on the `/ha` page, over SMB (the Samba share add-on): the ticked packages to `packages/`, the dashboards to `world_assistant/dashboards/`, and optionally the missing `secrets.yaml` lines (backed up first, existing lines never changed, differing ones reported). Unchanged files are skipped. A `configuration.yaml` snippet turns the copied dashboards into YAML dashboards that update with each copy. Tested against a real Samba server in CI. Adds `smbprotocol` (with `cryptography`) to the locked dependencies.

- **`scripts/mqtt-ca.sh`**: your own certificate authority for MQTT over TLS (no domain or DNS provider needed): a 10-year CA kept in the secrets folder and a 5-year broker certificate for every name and IP you give, written where Mosquitto reads it (`--out`, `--restart` to restart it); re-running renews the broker certificate with the same CA, so clients need no change. World Assistant trusts that CA by itself (alongside the public roots), and Settings > MQTT offers `ca.pem` to download for the OVMS module and Home Assistant.

### Fixed
- The pollen grid could stop refreshing: points the pollen service cannot answer were "never fetched", so tried first on every run, and four failures in a row ended the run before any other point. Now a failing point waits (2 days, doubling to 30), only failures of points that have worked before end a run early, and the job reports which points failed.
- The Pollen dashboard's forecast-point map labels each point with its allergy risk (0 to 10) instead of its initials ("PP5"), and shows every point instead of grouping them into "+4" bubbles. Copy the new `pollen.yaml` (the Home Assistant page marks it as changed). The pollen map's legend now says what grey means (no forecast nearby).

## [0.14.0] - 2026-10-22

### Added
- **The web pages are an app.** Installable on a phone (Add to Home Screen) with its own icon, a bottom tab bar (Buses, Trains, Map, Fuel, More) and a More sheet on phones, a top bar with every page on wider screens, safe-area support, and a service worker that keeps the pages available offline over HTTPS (it never caches the API). `/` opens the map, or Settings before home is set.
- **Settings** replaces Status (`/status` redirects): a **setup wizard** (home, keys, Home Assistant), the Setup list, a **Keys** tab where data-source keys can be pasted and saved from the browser (kept in `WA_DATA_DIR/keys`, used at once, never shown again), Backups and updates, and System (version, worker heartbeat, raw status).
- **Graph page** (`/graph`): the Neo4j knowledge graph as an interactive node and edge view (drag, pinch, zoom, tap for details, double-tap to expand), search, ready-made questions, and read-only Cypher (`POST /v1/graph/query`; writes are refused and the session is read-only). The Setup list shows whether the graph is on.
- **Tailscale** (`docker-compose.tailscale.yml`): World Assistant on your tailnet as its own device with HTTPS (`https://world-assistant.<tailnet>.ts.net`). In a browser there the pages need no token; Siri Shortcuts, MCP and the API still do ([docs/tailscale.md](docs/tailscale.md)).
- **MQTT over TLS**: `WA_MQTT_TLS` (and `WA_MQTT_TLS_CA` for a private CA) for World Assistant's own connection, a TLS listener on 8883 in the bundled broker once certificates are in place, and a guide from the car's OVMS module through a forwarded port to Home Assistant ([docs/mqtt-tls.md](docs/mqtt-tls.md)).
- `setup.sh` also asks about the knowledge graph and Tailscale. CI now runs Neo4j and checks every ready-made question, and checks that a key saved from Settings is used and that another site cannot save one.

### Changed
- `deploy.sh` and `setup.sh` create `WA_DATA_DIR/keys` for the app; `docker-compose.yml` mounts it at `/keys`.
- The Home Assistant page has Copy beside Download for the packages too (to paste into a new file in Studio Code Server or the File editor), and every Copy button now works over plain `http://` on the home network, where browsers withhold the clipboard API.

## [0.13.0] - 2026-10-22

### Added
- **No token on the home network.** The web pages and API let in requests that come straight from a private, loopback or Tailscale address, for a home-network host name, and (for changes) from this server's own pages; the token box disappears. Anything through a reverse proxy, another site's page, or a request from outside still needs the token. `WA_LOCAL_NO_TOKEN=false` turns it off; `WA_LOCAL_HOSTS` adds host names ([docs/security.md](docs/security.md#access)).
- **`scripts/setup.sh`**: first-time setup as questions (folders, Home Assistant broker, routing, each key with what it unlocks and where to get it, typed without echo), then it starts the stack. Safe to re-run; Enter keeps each answer.
- **Setup checklist** on `/status` (`GET /v1/setup`): every source, Home Assistant, routing and your home place, shown as working, set up and waiting, failing (with the error) or not set up (with what it would unlock and where to get a key).
- **Home Assistant page** (`/ha`): the broker to use, the packages to copy (with download buttons), the exact `secrets.yaml` lines for this server (token included on request), and each dashboard to copy into the raw editor, all from the running version. It marks files changed since the version you last copied (`homeassistant/manifest.json`, checked by a test).
- **Bundled MQTT broker**, optional: `docker-compose.mqtt.yml` runs Mosquitto with a generated password and anonymous access off, for a Home Assistant that has no broker yet.

### Changed
- Home Assistant is optional: the stack no longer insists on `WA_MQTT_HOST`. Empty means nothing is published, and the Setup list says so.
- On a new install, fuel stations, bus stops and postcodes load when the worker starts, not at the nightly run.
- `.env.example` keeps data and keys in this folder by default (`./data`, `./secrets`, both ignored by git and Docker builds), with the Unraid paths as a comment. Existing `.env` files are untouched, and the compose defaults are unchanged.
- The smoke check sends its "no token is refused" requests as if from outside; `init-secrets.sh` lists every key with what it is for.
- TfL road disruptions keep the area TfL says they affect (its `geometry`, a MultiPolygon) when there are no street lines, instead of a single point, so trip conditions and `GET /v1/roads/tfl/at` now find long works anywhere along their length. Run the TfL sync (or wait 15 minutes) after updating. The probe reports how many are stored as lines, areas and points ([docs/tfl-roads.md](docs/tfl-roads.md)).

## [0.12.1] - 2026-10-22

### Changed
- Trip conditions ignore TfL disruptions it rates "No impact" (unless the road is closed). The TfL probe also shows the shapes of `roadDisruptionLines` and `roadDisruptionImpactAreas`, which the parser does not use yet.

### Added
- A tracker for the way back. In bus setup, **Also track the way back** (default 15:00 to 17:30) creates a second tracker, `<name>-return`, from the other side of the stop you got off at to the other side of the stop you got on at, with its own times and record. The worker now also polls 14:00 to 18:59.
- Live bus positions for the tracker (Bus Open Data Service, needs the `bods_api_key`): GPS position, bearing and destination of the buses being followed, kept 30 days (`GET /v1/bus/trackers/{id}/positions`) and added to events about them. Collected only inside each tracker's own journey window, only for buses TfL shows heading for its stops. A "reported at an earlier stop" event whose bus is still heading the same way is marked as the predictions shifting. `WA_BUS_LIVE_ENABLED=false` turns it off. Migration 0026 ([docs/bus.md](docs/bus.md)).

## [0.12.0] - 2026-10-22

### Added
- Trip conditions: what is on the way. `GET /v1/trips/{id}/conditions` (and `GET /v1/conditions` for every routed trip) combines National Highways events on the trip's corridor, TfL road disruptions near the route and Environment Agency flood warnings crossing it, as **now** (clear, minor, disrupted, severe, with the items and a sentence to read aloud) and **planned** (works starting later, `days` ahead), with a `when` option for a later departure. Shown on the Places page, as two Home Assistant sensors per routed trip (status; planned works count) under a new Trips device, and as an assistant tool, `road_conditions` ([docs/trip-conditions.md](docs/trip-conditions.md)).
- London road disruptions from TfL (works, closures, collisions, incidents), every 15 minutes, kept with where they are: `GET /v1/roads/tfl` lists them and `GET /v1/roads/tfl/at` finds those near a place or point. A probe (`python -m app.integrations.tflroads.probe`) shows the real response. `WA_TFLROADS_POLL_ENABLED` turns it off. Migration 0025 ([docs/tfl-roads.md](docs/tfl-roads.md)).

### Changed
- Bus tracker events say where and which way. "Ended early", "Turned round", "Changed direction" and "Started its route" now name the stop and where that bus was heading ("towards Kingston" or "towards Sutton"); the Home Assistant "ended early" sensor includes it too. "Went backwards along the route" is renamed "Reported at an earlier stop than before", with the two stops, because it is usually the live predictions shifting rather than the bus going backwards. Events recorded before this update keep their old, shorter text.
- On the Home Assistant Events dashboard, each event name links to its page (details and tickets) when it has one, for events near home and near the extra places. Replace `events.yaml` in your dashboards; the extra places also need the new worker, which now publishes each event's link.

### Fixed
- The Fuel and Trains and buses dashboards no longer show "Entity not found" when the optional fuel or bus package is not installed: the dropdown and bus-time controls appear only once their package exists, with a one-line note until then.
- Bus tracker setup: a stop served in both directions now appears twice in the boarding list as "Stop (towards X)" and "Stop (towards Y)". Before, both read the same, so you could not tell which direction you were choosing and the destinations only ran one way.

## [0.11.1] - 2026-10-22

### Added
- An `electricity` tool for the assistant (`/v1/ask/electricity`, Siri, MCP): the price now and next, the next cheap and peak times, the cheapest block of hours and Saving Sessions, as a spoken sentence.

### Changed
- The Home Assistant sensors "Next cheap window starts/ends" and "Next peak window starts" are renamed "Cheap window starts/ends (current or next)" and "Peak window starts (current or next)", because they show the window you are in when you are in one. Entity ids are unchanged.

### Added
- `docs/ha/wa_energy_plan.yaml`: a Home Assistant package that sets the charge target of the three Cosy charging slots on a Deye/Sunsynk inverter from expected use and the solar forecast. Dry run until switched on. It also reads the Saving Session sensors: a joined evening session raises the afternoon charge target, a low battery during a joined session sends a notification, and a joined session outside 16:00-22:00 is flagged ([docs/energy.md](docs/energy.md)).

### Removed
- The Free Electricity sessions list: its community-run source stopped updating in October 2025 and Octopus now announces these on a calendar. Its sensors, setting (`WA_ENERGY_FREE_SESSIONS_ENABLED`) and probe section are gone; stored rows are cleared on the next sync.

## [0.11.0] - 2026-10-22

### Added
- **Energy page** at `/energy`: the rate and band now, a 48-hour chart coloured cheap / standard / peak, the next cheap and peak windows, the cheapest block of 1 to 6 hours, and Octopus sessions.
- Octopus **Saving Sessions** (announced events, and whether you joined) and **Free Electricity** sessions (from a community-run list; switch `WA_ENERGY_FREE_SESSIONS_ENABLED`). `GET /v1/energy/sessions`, and new Home Assistant sensors for each session being on now and the next one. World Assistant never joins a session. Migration 0024.
- `python -m app.energy.probe` prints what Octopus really returns for your account (account number and tokens blanked), to check the session parsers against the real service ([docs/energy.md](docs/energy.md)).

## [0.10.0] - 2026-10-22

### Added
- Energy supply module (phase 1): Octopus unit rates for your tariff (fixed, Cosy, Go, Agile) every 30 minutes, banded cheap / standard / peak from the rates themselves. `GET /v1/energy/now`, `/rates` and `/windows`, and an **Energy** device in Home Assistant with the current rate and band, a cheap-now flag, the next change, the next cheap and peak windows and the cheapest block, for Home Assistant to act on; World Assistant never controls the inverter. New secret `octopus_api_key`, settings `WA_OCTOPUS_ACCOUNT` / `WA_OCTOPUS_TARIFF`, switch `WA_ENERGY_POLL_ENABLED`. Migration 0023 ([docs/energy.md](docs/energy.md)).

## [0.9.0] - 2026-10-22

### Added
- Live buses anywhere in Great Britain from the Bus Open Data Service: `GET /v1/live-buses` and a `buses` map layer show where every bus that reports a position is right now, near a place. Needs a free BODS key in the new `bods_api_key` secret; without one the map says so and the rest is unaffected. Nothing is stored; positions are cached for 10 seconds ([docs/live-buses.md](docs/live-buses.md)).
- Every UK postcode with a position, held locally from Ordnance Survey's Code-Point Open (about 1.7 million, Open Government Licence). Adding a place by postcode now looks it up in your own database first and falls back to postcodes.io only for one the file lacks, so day-to-day use no longer sends your postcodes anywhere. `GET /v1/postcodes/{postcode}` and `/v1/postcodes/near` (the nearest postcodes to a point, for a dropped pin). A daily job downloads the file when the table is empty or a month old; `WA_CODEPOINT_POLL_ENABLED` turns it off. Migration 0022 ([docs/postcodes.md](docs/postcodes.md)).
- Flood warnings from the Environment Agency (England; no key needed), every 15 minutes: `GET /v1/floods` lists them, `GET /v1/floods/at` says which cover a place or point, and the map draws each warned area as a coloured shape. Boundaries are fetched once and kept. In the knowledge graph a warning `COVERS` the places inside it and `CROSSES` the corridors it runs over, and `/v1/graph/disruption` reports both ([docs/floods.md](docs/floods.md)). `WA_FLOOD_POLL_ENABLED` turns it off. Migration 0021.
- Where every bus stop, station and ferry terminal in Great Britain is, from NaPTAN, the national register (about 400,000 records, Open Government Licence). The bus tracker had stop ids and names but no positions, which is why stops could not be drawn or measured from. A job checks daily and reloads the register into `naptan_stop` when it is empty or a week old (migration 0020); `GET /v1/stops/near` finds stops around a point and `GET /v1/stops/{atco}` looks one up. The map gains a `stops` layer, and the knowledge graph gains `BusStop` nodes for the stops a tracker watches, joined to the tracker (`CALLS_AT`) and to any place they are near ([docs/naptan.md](docs/naptan.md)). `WA_NAPTAN_POLL_ENABLED` turns the job off.

## [0.8.0] - 2026-10-22

### Removed
- The PostGIS image installs `postgis_tiger_geocoder`, a United States census address geocoder, which puts `tiger` on the search path along with its own `place` table — ours won only because `public` comes first. Nothing here geocodes anything, so migration 0019 drops it: 34 empty tables and a trap gone.

### Changed
- PostGIS, installed since the first migration but never used, now does the distance work. Every table with coordinates gains a point column Postgres keeps in step with lat and lon by itself, and a spatial index over it (migration 0018), so "within 10 km, nearest first" is an indexed query instead of reading the table and filtering in Python. Fuel searches, the map layers and the graph's proximity all use it, and distances are now measured on the spheroid, so they move by a few hundred metres ([ADR 0007](docs/adr/0007-postgis.md)). CI tests against the same PostGIS image production runs.

### Added
- An optional knowledge graph (Neo4j) holding how the data joins up: trips to the places and corridors they use, closures to the corridors they sit on, stations near a place or along a corridor ([docs/graph.md](docs/graph.md), [ADR 0006](docs/adr/0006-knowledge-graph.md)). `GET /v1/graph/neighbours` and `GET /v1/graph/disruption` follow those connections; `docker-compose.graph.yml` and `WA_GRAPH_ENABLED` turn it on. It owns no data — the `graph_build` job rebuilds it from Postgres twice an hour, so it is safe to drop and is left out of the backup.
- A map of everything with coordinates, a layer at a time: saved places, Home Assistant locations, road corridors, open road events, motorway signs, fuel stations priced from cheapest to dearest, and the pollen lattice. The `/map` page draws them over street tiles, behind `GET /v1/map/layers` and `GET /v1/map/geojson` ([docs/map.md](docs/map.md)). Leaflet is served from the app itself, so no page loads a third-party script; tiles are the only outside fetch and `WA_MAP_TILE_URL` points them wherever you like.
- A Home Assistant dashboard for the Leaf (`homeassistant/dashboards/leaf.yaml`, [docs/leaf.md](docs/leaf.md)): what you need before driving on the first view, diagnostics on the second. A "Before you go" card stays silent unless the car is plugged in but not charging, low, unlocked, open, short of 12V, alerting on cells, or off its tyre pressures. Fed by OVMS; it needs nothing from World Assistant.

### Fixed
- The coloured trail on the Leaf map (24 hours of positions) looked like something marked on the road, so the dashboard and [docs/leaf.md](docs/leaf.md) now say what it is and how to turn it off.
- Dashboard cards showed a red "Configuration error" where a note sat in the card's footer: Home Assistant accepts only `buttons` and `graph` there. The notes on the Leaf and Trains and buses dashboards are now cards of their own, and a test rejects any other footer type.
- A pollen test pinned a forecast to a fixed date, so it passed on the day it was written and failed from the next day on. It now uses the day the app is living in.

## [0.7.6] - 2026-10-21

### Added
- Bus tracker records where along the route each run first appears in the TfL feed (anomaly type `first_seen`: stop, position, seconds ahead). It is bookkeeping, left out of `/anomalies` unless asked for with `?type=first_seen`.

### Fixed
- Bus tracker: each bus now claims the nearest scheduled departure that no earlier bus has claimed, so two bunched buses no longer count as one and leave a neighbouring departure marked as missed. Buses are also matched to departures in order (they do not overtake, so a late bus cannot take a slot ahead of the bus in front). Re-running the backfill corrects the slot of buses already on record.

## [0.7.5] - 2026-10-21

### Changed
- The Unraid icons' cable is now a green USB data cable with a USB-A plug (it looked like a power lead).

### Added
- Add routes and places to watch for events from Home Assistant: a Manage dashboard with four helpers and four scripts (`world_assistant_manage.yaml`, `manage.yaml`), backed by `POST /v1/ha/routes` (two positions, saved as places and a trip each way, routed locally and capped by `WA_HA_ROUTES_MAX`, default 12) and `POST /v1/ha/routes/delete`. A route's corridor becomes a roads sensor. Home Assistant's places and trips are marked (`place.origin`, `trip.origin`, migration 0017; ids start `ha-`) and never touch the ones made on the web pages. New secrets `wa_routes_url` and `wa_routes_delete_url`.
- Bus tracker: the deadline, earliest departure and the time to stop showing the chance are settings you can change without resetting anything: `POST /v1/bus/settings`, the Bus page, or three time helpers on the Trains and buses dashboard (new `homeassistant/packages/world_assistant_bus.yaml`, secret `wa_bus_settings_url`).
- Bus tracker: a daily log of buses that end a trip early while watched (`/v1/bus/trackers/{id}/terminations`, an `ended_early` sensor and a section of the Bus tracker card): where each was last seen, when, whether it turned back or stopped reporting, and where it next appeared.
- Bus tracker: the chance the next bus gets to the target by the deadline (from how often that departure made it over the last 30 days; weekday mornings only, hidden after `show_until`, default 08:00), a success/failure log of every scheduled bus (`/v1/bus/trackers/{id}/log`), and `chance` and `record` sensors in Home Assistant. Reaching the stop on either side of the road now counts (`either_side`, on by default), and the Bus tracker card on the Trains and buses dashboard shows the next bus at your stop, the chance, the last bus and the recent record.
- A fuel dropdown on the Fuel dashboard (Petrol or Diesel) in place of the two fixed cards: one price card follows it. Needs the new `homeassistant/packages/world_assistant_fuel.yaml`.
- Icons in Unraid's Docker tab: a simple green earth with a USB cable hanging off it, and a coloured band naming each supporting container's role (worker, database, cache, routing, vault). The compose files carry the `net.unraid.docker.icon` labels, the api also gets a `net.unraid.docker.webui` link to `/status`, and the icons are served by the api at `/icons/<name>.png` without a token. Sources in `deploy/unraid/`.

### Fixed
- The new log of buses that end early no longer fills with the previous day's buses on the first poll of the morning: a trip silent for more than ten minutes (overnight, say) is not logged as having ended.
- Bus tracker recorded no successful runs at all (0 of 5 on a morning when six buses reached the target in time). The buses on the stretch you travel were labelled `inbound` by TfL while set-up had stored `outbound`, and the matcher ignored every trip whose label differed. It no longer looks at the label: the stops are specific to one side of the road, so a bus seen at your boarding stop and then at the target (in that order) counts. A bus seen at the target before the boarding stop does not.
- `python -m app.integrations.bus.backfill <tracker> [day]` records a day's successful runs from the buses the tracker still remembers, for a day the live matching missed. Safe to repeat.
- Bus tracker sensors in Home Assistant were only published by the poller during its 05:00-11:00 window, once per worker start, so they were missing after re-adding MQTT and went quiet the rest of the day. They now come from the regular Home Assistant publisher, with the day's answer (last bus, buses seen, summary) available at any hour; the live values still only update while polling. Entity ids are unchanged (`sensor.wa_bus_<tracker>_…`).
- Fuel in Home Assistant showed only the 944 stations (of about 7,900) that had updated their price in the last 48 hours, because most stations only report when a price changes. It now publishes every price up to 30 days old (`WA_HA_FUEL_MAX_AGE_H`), each with its age. The Fuel dashboard looks within 16 km and at prices up to a week old by default (`radius_km` and `max_age_h` at the top of each card) and marks prices more than two days old with ⚠️. The national median still uses only prices under 48 hours old.
- Home Assistant no longer loses the attributes of busy fuel and road-event map tiles. A tile whose
  data would pass the recorder's 16 KB limit is now split across several sensors (`…_p2`, `…_p3`);
  the dashboards read them all.
- The events package survives the home zone not being loaded at start-up (it asked with an empty
  location and stayed empty for an hour). It now re-checks a minute after start-up, and a failed
  request shows as unknown instead of 0.


## [0.7.4] - 2026-10-21

### Removed
- `scripts/pollen-survey.py` (about 100 provider calls, far over the 40 a day the key allows).

### Fixed
- Pollen provider limit. The provider allows only 40 requests a day on a key; the nationwide grid (about 150 a day as first built) and the four-hourly saved-place job exceeded it. All pollen requests now draw from one daily budget of 36 (`WA_POLLEN_DAILY_LIMIT`). Stored forecasts are read by date, so a point stays usable for four days; the grid job refreshes the 12 stalest points twice a day (`WA_POLLEN_GRID_CALLS_PER_RUN`), keeping 6 requests back (`WA_POLLEN_RESERVE_CALLS`); the saved-place history job runs once a day.

## [0.7.3] - 2026-10-21

### Added
- Extra places for events in Home Assistant. `rest_command.wa_add_location` registers a name and position (`POST /v1/ha/locations`); the worker publishes a `sensor.wa_events_place_<name>` for each over MQTT and the events dashboard lists whichever exist. Capped at 8 (`WA_HA_LOCATIONS_MAX`); each is searched every 12 hours (`WA_HA_LOCATION_REFRESH_H`), about 4 provider requests a day per place; places not renewed within 30 days are dropped, and within 7 days once 75% of the cap is used. Migration 0016.
- Each pollen forecast point is a Home Assistant sensor with a position (`sensor.wa_pollen_point_<lat>_<lon>`), and the pollen dashboard has a map card of them: tap a marker for the allergy risk and each allergen by day.
- Nationwide pollen. The worker fetches a lattice of about 51 points across Britain three times a day (gently, 10 s apart; setting `WA_POLLEN_GRID_DEG`), stores it (`pollen_grid`, migration 0015) and publishes it to Home Assistant as pollen tiles plus a colour-coded map image (`image.wa_pollen_map`). New `GET /v1/pollen/grid`; `/v1/pollen/near?lat&lon` answers from the nearest stored point, so the provider never sees anyone's location.
- Pollen dashboard: the map, each viewer's pollen this week, and a history graph for the home location, fed by a static package (`homeassistant/packages/world_assistant_pollen.yaml`, no setup or secrets).

### Changed
- Events in Home Assistant now use `zone.home` and no longer follow each person. `scripts/ha-setup.py` and the per-person location and events sensors are gone, replaced by a static package, `homeassistant/packages/world_assistant_events.yaml` (two secrets, no generator). Entities are `sensor.wa_events_home_upcoming` and `_next`.
- Home Assistant location is now per person. The fixed package file is replaced by `scripts/ha-setup.py`, which writes events sensors for each person you list (their app location while away, else `zone.home`). The fuel, roads, events and pollen dashboards show each viewer data for where they are, picking their person by the logged-in user's name. Someone 100 miles from you no longer sees your pollen count or petrol prices.

## [0.7.2] - 2026-10-21

### Added
- Pollen, events, fuel and roads in Home Assistant follow where you are: the Home Assistant mobile app's location when you are away from home and it reports one, otherwise `zone.home`. A new `sensor.wa_location` in the package (one edit: your `person` entity) drives the dashboards and the requests.
- Pollen and events follow Home Assistant's own location. `/v1/pollen/near` and `/v1/events/near` accept `lat` and `lon` (instead of a place); a Home Assistant package (`homeassistant/packages/world_assistant.yaml`) fetches them for `zone.home` hourly, with a sensor per allergen so each has its own history. The per-place pollen and events devices over MQTT are gone, and the pollen and events dashboards now use the package. See docs/home-assistant.md.
- Road closures and speed restrictions in Home Assistant. The worker publishes every live National Highways event nationwide, with positions, in map tiles, and the roads dashboard lists the closures and speed restrictions near Home Assistant's own `zone.home` (changeable at the top of each card). New `GET /v1/status/events/map` (unplanned events, and planned works starting within `planned_days`, default 7).

## [0.7.1] - 2026-10-21

### Added
- Ready-made Home Assistant dashboards, one per data source (pollen, fuel, trains and buses, roads, events) in `homeassistant/dashboards/`, using only built-in cards. See docs/home-assistant.md.

### Fixed
- Home Assistant sensors now also send `default_entity_id`, because Home Assistant is retiring `object_id` in MQTT discovery. Entities already created keep their ids.

## [0.7.0] - 2026-10-21

### Added
- Home Assistant sensors over MQTT discovery for fuel prices, train and bus departures, road works, pollen and nearby events. Nothing uses World Assistant's default place: fuel is published nationwide (in half-degree map tiles with station coordinates) for Home Assistant to filter by its own zones, and pollen and events are published for each saved place. The worker reads the service's own API every `WA_HA_INTERVAL_MIN` minutes (default 5) and publishes retained sensors that go `unavailable` if the worker stops. `WA_HA_PUBLISH=false` turns them off. New `GET /v1/fuel/all`. See docs/home-assistant.md.

## [0.6.1] - 2026-10-21

### Fixed
- The automatic update job's signature check now matches the release workflow's identity whatever the capitalisation of the repository name. In 0.6.0 it looked for `World-assistant` while releases are signed as `world-assistant`, so every update would have been refused.

## [0.6.0] - 2026-10-21

### Added
- This is a new public repository. The project's earlier history (releases 0.1.0 to 0.5.0) stays in a private archive; the first release from here is 0.6.0.
- Documentation brought up to date for a public repository: a README that explains the purpose of the project, current architecture, schema and code layout, data credits and licences, and what automatic updates trust. Issue and pull request templates, and `docs/repo-settings.md` (branch protection for `main` and the other repository settings). Examples and tests use central-London coordinates, never real home ones.
- The server's project folder can be a git checkout of the repository (`scripts/git-setup.sh`, once, with a read-only GitHub token), updated with `scripts/sync.sh [tag|commit] [--deploy]`. The nightly automatic update moves the folder to the release tag along with the image, and back again on a rollback, so compose and script changes arrive too. See docs/runbook.md.
- Automatic updates for the server itself, from a switch on the `/status` page. A nightly job (`scripts/auto-deploy.sh`, set up once with Unraid's User Scripts plugin) updates to the newest signed release when the switch is on: it verifies the signature, takes a backup, updates, runs the page and endpoint check, rolls back (including the database change) if that fails, and reports the result on the status page. A release that needs newer compose files is refused (`COMPOSE_REVISION`). New `/v1/auto-deploy` endpoints and an `app_setting` table (migration 0014). See docs/runbook.md.
- Comfort tests. CI has a new `stack` job that starts the real compose stack on PostGIS (pollers off), runs the migrations, requests every page and every GET endpoint (`python -m app.smoke`: no 500s, pages complete, the API refuses a missing or wrong token, a missing key gives a clear 503, never a crash), takes the last migration down and up, does a backup, delete and restore round trip, and loads every page in a real browser with the token entered (`scripts/page-check.py`, no script errors). The release gate and automatic merging now require `stack` as well as `test`, `secrets` and `image`. `deploy.sh` runs the read-only smoke check once the api is healthy and fails if anything does. `tests/test_migrations_chain.py` guards against two migration heads.
- Optional automatic updates (repository variable `AUTO_UPDATES=true`): safe Dependabot updates merge once CI passes, CI runs on `main` after, and a changed Python base image opens a GitHub issue. A second variable, `AUTO_RELEASE=true`, also releases the next patch version through the gated release. Dependabot now checks daily and also watches the compose files. Releases record their base image. `deploy.sh` pulls the latest third-party and base images. See docs/runbook.md.

### Changed
- The worker records its heartbeat as soon as it starts, so `/status` shows it alive straight after a deploy instead of up to five minutes later.
- The release workflow is gated: it refuses unless the commit is the tip of `main`, the version is new and CI (`test`, `secrets`, `image`) has passed on it, waiting up to 5 minutes for CI. No override. See docs/runbook.md.

## [0.5.0] - 2026-10-21

### Changed
- Redis is now part of the stack: a password-protected `wa-redis` with no published port, password in the new `redis_password` secret (`init-secrets.sh` creates it). `WA_REDIS_URL` is optional; remove it from `.env` to use the bundled one. See docs/runbook.md.
- Home is the default place: the fuel, events and pollen endpoints and assistant tools no longer need a `place` (they use your home), and the `/fuel`, `/events` and `/pollen` pages start on it. See docs/assistant.md.

### Added
- Pollen: pollen levels by allergen and the allergy risk near a saved place, today and the next three days, from the Austrian Pollen Information Service (free personal key in `pollen_api_key`; non-commercial use, credit shown). `/pollen` page, `/v1/pollen/near` (add `hourly=true` for hourly risk), assistant tool `pollen`, probe `python -m app.integrations.pollen.probe`. Cached for 4 hours.
- Pollen history: a worker job fetches for every saved place every 4 hours and records one value per day and pollen, kept for 90 days. `/pollen` shows a total graph and a graph per pollen (with the allergy risk), and `/v1/pollen/history` returns the data. `python -m app.pollen_history` records once, now. New migration 0013.

## [0.4.0] - 2026-10-21

### Added
- Events: what is on near a saved place, from Skiddle and Ticketmaster (free keys in `skiddle_api_key` and
  `ticketmaster_api_key`; either alone works), merged and sorted by date; one source failing still shows the other. `/events` page,
  `/v1/events/near`, assistant tool `events_near`, and probes for each source (`python -m app.integrations.skiddle.probe`, `...ticketmaster.probe`).
  Results are cached for 10 minutes and never stored.
- OpenBao, bundled and optional: `docker-compose.openbao.yml` runs it beside the app, and
  `bash scripts/openbao.sh setup` initialises it, creates the KV store and a read-only AppRole, copies your
  secret files in and points the app at it. Unseal keys and the root token are shown once and never stored;
  `deploy.sh` unseals after upgrades only if you opt in with `store-key`. See docs/openbao.md.
- Optional auto-unseal for the bundled OpenBao: `scripts/openbao.sh store-key` starts a helper that unseals it after any restart.
- `python -m app.secretsctl dump-json` (refuses a terminal) for moving secrets into a vault.

## [0.3.0] - 2026-10-21

### Added
- Backups: built in, no extra tools. A **Back up now** card with downloads on `/status`, `/v1/backups`,
  `python -m app.backup create|list|restore`, and `scripts/deploy.sh`, which backs up before every
  upgrade and stops if the backup fails. Keeps the latest 14. Restores are one transaction. See docs/backup.md.
- Fuel: the cheapest station nearby, any brand, is always shown at the top of `/fuel` and returned as
  `cheapest_overall` by the API, whatever brands are ticked. New "prices no older than" control.

### Fixed
- Fuel: brand spellings from the feed are merged (`SAINSBURYS`, `Sainsbury's`), shown evenly, and the major
  supermarket brands count as supermarkets whatever the feed's flag says.

## [0.2.0] - 2026-10-20

### Changed
- Secrets: one lookup for every key and password (`app/secretstore.py`). Files in the secrets directory
  (with or without `.txt`) remain the default; optional key vault backends: HashiCorp Vault / OpenBao
  (KV v2, token or AppRole) and a generic command backend for 1Password, Bitwarden, Azure, AWS, Google
  and others. Vault outage falls back to the last values and then to files. New
  `python -m app.secretsctl check` shows where each secret comes from without printing values.
  `init-secrets.sh` now also creates `nr_access_token`; vault sign-in files are only mounted via the optional `docker-compose.vault.yml`. See docs/secrets.md.
- All browser pages (`/status`, `/bus`, `/trains`, `/fuel`, `/places`) share one cleaner look with a nav bar,
  cards and automatic dark mode. No data or API changes.

### Added
- Fuel: tick brands (or all supermarkets) on `/fuel` to compare the absolute cheapest with the cheapest of your
  chosen brands, with the extra pence per litre, the extra cost for a fill and the distance saved.
  API: `brands=Tesco,Asda`, `supermarkets=true`, `fill_litres` on `/v1/fuel/near` and `/along`; the
  assistant `fuel_cheapest` tool takes `brands` and `supermarkets`.
- Bus tracker: follows every bus on a line, times legs between configurable stops of interest,
  records which buses reached a target stop before a deadline (weekday polling window), captures
  terminations and turnarounds, slot reliability history. See docs/bus.md.
- Question answering: `/mcp` (MCP over HTTP) and `/v1/ask/{tool}` (Siri Shortcuts, scripts) for fuel,
  trip cost, trains and buses, with spoken-sentence answers. See docs/assistant.md.
- Rail departures from National Rail (any GB station, via the Rail Data Marketplace) as well as TfL: boards have a `source`; probe tool
  `python -m app.integrations.rail.probe`.
- `/trains`: optional display grouping by platform (browser-only setting; the data is unchanged).
- `/bus` page: live buses, target-stop run, leg times, reliability, events, and guided tracker setup
  (pick stops from a list; ids, direction and return side are worked out).
- MQTT publishing with Home Assistant discovery for bus tracker sensors.
- TfL departures: saved boards (station, lines, destination filter), live departures with delay and
  status, line status, station search, `/trains` page, probe tool.

### Fixed
- Train boards keep showing the last good reading (up to 15 minutes) when the provider errors.
- Rebuilding a route could fail with a 500 (duplicate corridor tag for an event); tagging is now idempotent.

### Changed
- Release: GitHub provenance attestation is optional (`ENABLE_GH_ATTESTATIONS`), since private personal repos cannot store it.
- All GitHub Actions moved to their latest releases (Node 24) and pinned to commit hashes.

### Fixed
- CodeQL workflow no longer fails on a private repository: it runs only when `ENABLE_CODEQL` is set, and uses CodeQL Action v4.

## [0.1.0] - 2026-10-20

First tracked release.

### Added
- National Highways adapter: closures, speed managed areas and message signs, polled
  (live every 10 minutes, planned every 2 hours), stored with history, tagged by corridor.
- Fuel Finder adapter: stations (daily) and prices (every 30 minutes), change history,
  cheapest-near-place and cheapest-along-corridor queries.
- Places, trips and vehicles with web pages and API; postcode lookup.
- Valhalla routing: trip to route to corridor; base journey time and road numbers.
- Trip cost for petrol/diesel and electric vehicles; detour-aware station ranking.
- Status page and API, job tracking, bearer-token authentication.
- Hash-locked dependencies, multi-stage non-root image, hardened compose override.
- CI, release workflow (build once, scan, SBOM in SPDX and CycloneDX, keyless signing,
  provenance), CodeQL, secret scanning, Dependabot.

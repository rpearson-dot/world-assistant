# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

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

# World Assistant

[![CI](https://github.com/rpearson-dot/World-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/rpearson-dot/World-assistant/actions/workflows/ci.yml)

**The other half of a smart home.** Home Assistant knows what is going on inside the house. World
Assistant knows what is going on outside it: the roads you drive, the fuel you buy, the buses and
trains you catch, what is on nearby, and what the pollen is doing to your hay fever. It runs on your
own server, keeps its own history, and answers in one place, whether you ask from a web page, a Siri
Shortcut, a script, an AI assistant, or a sensor on your Home Assistant dashboard.

## Why it exists

Everything outside the house that affects your day is scattered. Road closures are in one government
feed, fuel prices in another, bus and train times in two more, events on commercial sites, pollen on a
European service. Each has its own key, rate limit, format and quirks, and almost none keep a history,
so you can never ask *"is this normal?"*.

World Assistant is a small, self-hosted gateway that:

- **talks to each source once, politely.** It respects every provider's limits, so ten questions
  do not mean ten calls;
- **keeps what is worth keeping.** Road events, fuel price changes, bus timings and pollen are stored, so
  you get trends and "how often is the 7:42 late?", not only "right now";
- **answers questions about *your* journeys and places, not the whole country.** *What is the M25 like
  towards Brighton right now? Where is the cheapest E10 along the way, and is it worth the detour? What will
  the trip cost in the petrol car versus the electric one? Did my bus make the stop before the deadline this
  week? What is on near home on Saturday? How bad is the birch pollen tomorrow?*
- **stays yours.** Your places, trips and vehicles never leave your server. There is no account, no cloud
  service and no telemetry; the only outbound calls are to the public data services.

It started as a personal project on a home Unraid server. Nothing in it is specific to Unraid, but it is
**UK-focused**: most of the data sources are British.

## What it does

| Area | What you get |
|------|--------------|
| **Roads** | National Highways closures, variable speed limits and message signs, polled and stored with history. Each event is tagged with the corridors (your regular routes) it affects. |
| **Fuel** | Every UK station's current prices from the government Fuel Finder service, refreshed every 30 minutes. Cheapest near a place or along a route, with the saving after the detour. |
| **Events** | What is on near a place (gigs, comedy, theatre, sport and more) from Skiddle and Ticketmaster, merged by date. |
| **Pollen** | Pollen by allergen and allergy risk for today and three days ahead, with a 90-day history and graphs. |
| **Places and trips** | Named places (by postcode or coordinates) and trips between them. Your **home** is the default place everywhere. |
| **Routing** | Optional self-hosted Valhalla turns a trip into a real road line, which becomes that trip's corridor. |
| **Vehicles** | Petrol/diesel (mpg) and electric (battery, health, miles per kWh). Trip cost, range check, and whether a cheaper station is worth the detour. |
| **Buses and trains** | Live bus tracking with on-time statistics, and train departure boards (TfL and National Rail). Departures, fuel prices and road closures are published to Home Assistant over MQTT, and fuel, roads, pollen (a map and a home graph) follow where each person is, and events show what is on near the home zone and any extra places you add (the app's location when away, else the home zone) ([how](docs/home-assistant.md)), with ready-made dashboards. |
| **Assistant** | The same data as spoken-style answers: `/v1/ask` for Siri Shortcuts and scripts, and `/mcp` for AI assistants that speak the Model Context Protocol. |
| **Knowledge graph** | Optional Neo4j holding how the data joins up — trips to corridors, closures to corridors, stations near places — so the assistant can follow connections rather than be told where to look ([how](docs/graph.md)). Derived from Postgres and safe to throw away. |
| **Live buses** | Where buses are right now, anywhere in Great Britain, from the Bus Open Data Service (a free key); a layer on the map and `GET /v1/live-buses` ([how](docs/live-buses.md)). The bus tracker above remains the TfL record; this is a live snapshot. |
| **Postcodes** | Every UK postcode with a position, held locally from Ordnance Survey Code-Point Open, so adding a place no longer needs an outside service; the nearest postcode to any point ([how](docs/postcodes.md)). |
| **Energy** | Your Octopus tariff's rates (Cosy, Agile, fixed) banded cheap / standard / peak, the next cheap window and cheapest block, Saving Sessions, on a page at `/energy` and as sensors for Home Assistant to run the battery from ([how](docs/energy.md)). |
| **EV chargers** | GRIDSERVE's chargers near your places, along a trip or by name: how many work and are free now, by connector (CCS, CHAdeMO, Type 2), how fast and the price, refreshed hourly; on the map and in the graph ([how](docs/ev.md)). |
| **Trip conditions** | What is on the way for a trip with a route: works, closures and incidents (National Highways and TfL) and flood warnings, now and planned ([how](docs/trip-conditions.md)). |
| **London road disruptions** | TfL works, closures and incidents with their position, every 15 minutes; ask what is near a point ([how](docs/tfl-roads.md)). |
| **Floods** | Environment Agency flood alerts and warnings for England, refreshed every 15 minutes, with each warned area drawn on the map; ask what covers home, or what crosses the way to work ([how](docs/floods.md)). |
| **Stops** | Where every bus stop, station and ferry terminal in Great Britain is, from the national NaPTAN register, held locally and refreshed weekly ([how](docs/naptan.md)). |
| **Map** | Everything with coordinates on one map, a layer at a time: places, people, corridors, closures, signs, fuel priced cheapest to dearest, stops and stations, pollen ([how](docs/map.md)). Leaflet is served from the app; only the tiles come from outside, and you choose where from. |
| **Web app** | Installable on a phone (Add to Home Screen) with a tab bar: Buses, Trains, Map, Fuel, Energy, Events, Pollen, Places, **Graph** (the knowledge graph as nodes and edges you can search, expand and query), **Home Assistant** setup, and **Settings** (a setup wizard, what is working, keys saved from the browser, backups, updates). No accounts, no token on the home network or in a browser on your tailnet. |
| **Operations** | One-click backups, signed releases, optional automatic updates with rollback, optional bundled key vault. |

Planned (see [docs/roadmap.md](docs/roadmap.md)): live-traffic journey times, MOT and tax reminders,
efficiency trends.

## How it works

```mermaid
flowchart LR
  S[Public data services] --> W[worker]
  W --> DB[(PostGIS)]
  A[api] --> DB
  A --> S
  U[Browser, Shortcuts, MCP] --> A
  W -.-> M[(MQTT / Home Assistant)]
```

A **worker** polls the feeds that are worth storing on a slow schedule and writes them to Postgres. An
**API** answers from the database, so a question never waits on a rate-limited upstream and keeps working
when one is down. Live services that are not stored (events, train boards) are cached briefly. It all runs
as a small Docker Compose stack: FastAPI, an arq worker, PostGIS Postgres and a private, password-protected
Redis, plus optional Valhalla and OpenBao. Details: [docs/architecture.md](docs/architecture.md).

## Is it for you?

It suits you if you run a home server, live in the UK, and like owning your data and your automations.
You will need to register for a few free API keys (see [docs/data-sources.md](docs/data-sources.md)); each
is optional, and a feature without its key simply says it is not set up.

It is **not** a hosted service, not a navigation or safety system, and not affiliated with any data
provider. Do not rely on it for anything where a wrong or late answer could hurt anyone. It is a
personal project: use it, fork it, send fixes, but expect no support level.

## Quick start

You need Docker with Compose. Keys are optional and free; Home Assistant is optional.

```bash
git clone https://github.com/rpearson-dot/World-assistant.git && cd World-assistant
bash scripts/setup.sh
```

`setup.sh` asks where to keep the data, whether you use Home Assistant (your broker, a bundled one, or none),
whether to add road routing, and for each key (with what it unlocks and where to get it, typed without echo).
Then it starts the stack and prints the address. Re-run it any time; Enter keeps each answer.

Then open `http://<server>:8010`. From your home network no token is needed. **Settings** starts a short setup wizard
(home, keys, Home Assistant) and its Setup list shows what is working, what each missing key would unlock and where to
get it; keys can be pasted straight in. Add your places (start with `home`) on
`/places`, and set up Home Assistant from `/ha`. Stops, stations and postcodes load within minutes of the first
start.

Prefer files? `cp .env.example .env`, edit it, `bash scripts/init-secrets.sh`, put keys in the files it lists,
`docker compose up -d --build`.

Do not expose the port to the internet. To reach it from your phone anywhere, add the Tailscale container
(`setup.sh` asks): it gets its own `https://world-assistant.<tailnet>.ts.net` address, for the web app, Siri
Shortcuts and AI assistants over MCP ([docs/tailscale.md](docs/tailscale.md)). Securing MQTT from a car's OVMS
module through to Home Assistant: [docs/mqtt-tls.md](docs/mqtt-tls.md).

## Staying up to date

- **By hand:** `bash scripts/sync.sh --deploy` (a git checkout) or `bash scripts/deploy.sh` (a copy).
  Both back up first and finish with a check that every page and endpoint still answers.
- **Released images:** releases are built once by GitHub Actions, scanned, signed and published with SBOMs,
  and deployed by digest, never by tag.
- **Automatically (optional, off by default):** a switch in Settings makes a nightly job install the newest
  signed release, with a backup first and an automatic rollback if the post-update check fails. On GitHub,
  Dependabot checks daily and can merge safe updates once CI is green.

All of it is in [docs/runbook.md](docs/runbook.md).

## Security in brief

From outside the home network every `/v1/*` route needs a bearer token, and refuses everything if none is set; on the home network the pages and API need none ([what counts as home](docs/security.md#access)). Secrets are files (or an
optional key vault), never environment variables or committed. The image runs as a non-root user and an
optional hardened profile makes it read-only. Dependencies are pinned by hash, actions by commit,
releases are signed (Sigstore) with SBOMs, and every push is scanned for secrets. See
[docs/security.md](docs/security.md) and [SECURITY.md](SECURITY.md).

## Documentation

- [Architecture](docs/architecture.md): components, data flow, schema
- [Runbook](docs/runbook.md): deploy, upgrade, roll back, automatic updates, troubleshoot
- [Data sources](docs/data-sources.md): each feed, its limits, licence and credit
- [Assistant](docs/assistant.md): Siri Shortcuts, MCP, the default place
- [Tailscale](docs/tailscale.md): the app on your phone anywhere, Siri and MCP over your tailnet
- [MQTT over TLS](docs/mqtt-tls.md): certificates from the car's OVMS module to Home Assistant
- [Bus tracker](docs/bus.md), [Trains](docs/trains.md), [Leaf dashboard](docs/leaf.md), [EV chargers](docs/ev.md), [Map](docs/map.md), [Knowledge graph](docs/graph.md)
- [Backups](docs/backup.md): the status-page button, the deploy script and restoring
- [Secrets and key vaults](docs/secrets.md), [bundled OpenBao](docs/openbao.md)
- [Security](docs/security.md): what is protected and how; supply chain
- [GitHub settings](docs/repo-settings.md): branch protection and the other repository settings
- [Roadmap](docs/roadmap.md) and [decisions](docs/adr/): what is parked, and why it is built this way
- [Changelog](CHANGELOG.md), [Contributing](CONTRIBUTING.md), [Security policy](SECURITY.md)

## API at a glance

From outside the home network, `/v1/*` routes need `Authorization: Bearer <api_token>`. The interactive reference is at `/docs`.

| Route | Purpose |
|-------|---------|
| `GET /v1/status` | Job runs, event counts, corridors, fuel counts, version, worker heartbeat |
| `GET/PUT/DELETE /v1/settings/keys/…` | Which data-source keys are set (never their values); save or forget one |
| `GET /v1/graph/overview`, `/search`, `/expand`, `POST /v1/graph/query` | The Graph page: explore the knowledge graph; read-only Cypher |
| `GET /v1/setup` | The setup checklist: each source, Home Assistant, routing and home, working or not, and where to get a key |
| `GET /v1/ha/setup`, `/v1/ha/files/…`, `/v1/ha/token` | Home Assistant files of this version, what changed when, and the values for `secrets.yaml` |
| `GET/PUT/DELETE /v1/places/{id}`, `/v1/trips/{id}`, `/v1/vehicles/{id}` | Manage places, trips, vehicles |
| `POST /v1/trips/{id}/route` | Route a trip and (re)build its corridor |
| `GET /v1/trips/{id}/cost?vehicle=` | Energy or fuel needed, range check, cost |
| `GET /v1/live-buses` | Buses reporting a position near a place or point (needs `bods_api_key`) |
| `GET /v1/postcodes/{postcode}`, `/v1/postcodes/near` | A postcode's position; the nearest postcodes to a point |
| `GET /v1/energy/now`, `/rates`, `/windows`, `/sessions` | Electricity rate now and ahead, cheap/peak windows |
| `GET /v1/trips/{id}/conditions`, `/v1/conditions` | What is on a trip's route, now and planned |
| `GET /v1/roads/tfl`, `/v1/roads/tfl/at` | TfL road disruptions; those near a place or point |
| `GET /v1/floods`, `/v1/floods/at` | Current flood warnings; those covering a place or a point |
| `GET /v1/stops/near`, `/v1/stops/{atco}` | Bus stops, stations and terminals near a point (nearest first), or one stop by its ATCO code |
| `GET /v1/fuel/near`, `/v1/fuel/along/{corridor}` | Cheapest fuel near a place or along a corridor; `vehicle=` ranks by saving after the detour |
| `GET /v1/events/near` | What is on near a place (`place` defaults to home) or a point (`lat` and `lon`) |
| `GET/POST /v1/ha/locations`, `GET /v1/ha/locations/events` | Extra places Home Assistant watches for events (capped, they expire) |
| `GET/POST /v1/ha/routes`, `POST /v1/ha/routes/delete` | Routes Home Assistant adds between two positions (both ways, routed locally, capped); their corridors become roads sensors |
| `POST /v1/bus/settings` | Change a bus tracker's deadline, earliest time and the time to stop showing the chance, without resetting it |
| `GET /v1/pollen/near`, `/v1/pollen/grid`, `/v1/pollen/history` | Pollen forecast (a place, defaulting to home, or a point with `lat` and `lon`) and the recorded history |
| `GET /v1/tfl/boards`, `/v1/bus/trackers/{id}/…` | Train departure boards and the bus tracker |
| `POST /v1/ask/{tool}`, `POST /mcp` | Spoken-style answers for Shortcuts, scripts and MCP clients |
| `GET/POST /v1/backups` | List, create and download backups |

## Data sources and credits

Road data from National Highways, fuel prices from GOV.UK Fuel Finder, bus and rail from Transport for
London and National Rail (Rail Data Marketplace), events from Skiddle and Ticketmaster, postcodes from
Ordnance Survey Code-Point Open and postcodes.io, stops from NaPTAN, flood warnings from the Environment Agency, maps © OpenStreetMap contributors, and pollen from the Austrian Pollen Information Service
(www.polleninformation.at, non-commercial use only). Each stays under its own licence and terms; see
[docs/data-sources.md](docs/data-sources.md). Raw feed data is never committed to this repository.

## Development

```bash
pip install --require-hashes --no-deps -r requirements-dev.lock
ruff check . && pytest
```

Tests that need Postgres or Redis are skipped unless `WA_TEST_DATABASE_URL` / `WA_TEST_REDIS_URL` are set.
CI also starts the whole stack and requests every page and endpoint. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

[MIT](LICENSE). No warranty. Third-party data stays under its own licence.

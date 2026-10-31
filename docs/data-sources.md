# Data sources

Check each provider's current terms before relying on this; the notes below are a summary,
not legal advice.

| Source | Used for | Access | Limits and notes |
|--------|----------|--------|------------------|
| **National Highways** (DATEX II v3.4 JSON, `api.data.nationalhighways.co.uk`) | Closures, speed managed areas, message signs | Subscription key, header `Ocp-Apim-Subscription-Key` | 10 calls a minute per key (the app uses 8). Closures and speed have no area filter, so they are fetched network-wide and filtered locally. Signs accept a bounding box. Speeds are in km/h. Check the provider's terms for reuse. |
| **Fuel Finder** (GOV.UK, `fuel-finder.service.gov.uk`) | Station details and fuel prices | OAuth client credentials | UK IP addresses only. 100 requests a minute, one at a time. Repeated failed logins cause a 5 minute lockout. Open Government Licence v3.0. The guidelines ask that the raw feed is not passed on, so this project stores it privately and exposes only derived answers. |
| **GRIDSERVE** (`api.gridserve.com/ocpi/v1`) | EV charging sites, each charger's live status, connectors and power, prices | Key in an `ec-subscription-key` header | Open data under the Public Charge Point Regulations 2023. Read hourly (about 2 MB each time). OCPI objects in a bare list. |
| **Transport for London** (Unified API, `api.tfl.gov.uk`) | Departures at a station or stop (`StopPoint/{id}/ArrivalDepartures`), line status | Optional free app key (`app_key`) | Works without a key at a low rate. Departures are cached for 30 seconds. Times with no zone are read as UK local time (`WA_TFL_NAIVE_TZ`). Check TfL's terms for reuse. |
| **Skiddle** (`skiddle.com/api/v1`) | Events near a place: gigs, club nights, comedy, theatre, sport and more | Free API key (secret `skiddle_api_key`), sent as `api_key` | Not stored: results are cached for 10 minutes. No numeric rate limit is published, so calls are limited to 30 a minute. Skiddle publishes no terms of use for the API that we found; read them when you apply for the key and keep the "Source: Skiddle" credit and the link to each event. Their web-api notes were archived in April 2024. `python -m app.integrations.skiddle.probe LAT LON` shows the real response. |
| **Polleninformation** (`polleninformation.at/api/forecast/public`) | Pollen by allergen (birch, grasses and so on) and allergy risk, today plus three days, for any saved place; set `WA_POLLEN_COUNTRY` (default `GB`) | Free personal API key (secret `pollen_api_key`), requested through the contact form on polleninformation.at/en/data-interface; sent as `apikey` | **The key allows only 40 requests a day** (`Too many requests. Maximum 40 requests per day`), so every call draws from one daily budget of 36 (`WA_POLLEN_DAILY_LIMIT`, counted per UTC day). A fetch returns four days, so results are cached per location for the day and stored lattice points are read by date for up to four days. A worker job refreshes the stalest points of a lattice of about 51 across Britain, 12 per run, twice a day (10 s apart; table `pollen_grid`), keeping 6 requests back for other uses, for Home Assistant's pollen map and nearest-point lookups. A worker job fetches for every saved place once a day and keeps one value per place, day and pollen for 90 days (table `pollen_reading`, included in backups) for the graphs on `/pollen`. **Non-commercial use only**, and public apps must credit "Austrian Pollen Information Service, www.polleninformation.at" (shown on `/pollen`). `python -m app.integrations.pollen.probe LAT LON` shows the real response. Each probe uses one of the day's 40 requests. |
| **Ticketmaster** (Discovery API v2, `app.ticketmaster.com/discovery/v2`) | Concerts, sport, theatre and shows near a place | Free consumer key (secret `ticketmaster_api_key`; the consumer secret is not needed), sent as `apikey` | Default quota 5,000 calls a day and 5 a second; we allow 240 a minute and cache results for 10 minutes. Not stored. Read their API terms of use and keep the link to each event. Types with no Ticketmaster equivalent (club nights, pubs) are not searched there. `python -m app.integrations.ticketmaster.probe LAT LON` shows the real response. |
| **National Rail** (Darwin, via the Rail Data Marketplace Live Departure Board) | Live departures for any station in Great Britain, by three-letter code (e.g. `SNL`) | Marketplace consumer key (secret `nr_access_token`), sent as `x-apikey` | Cached for 30 seconds. No station-name search on the marketplace. `WA_NR_BASE_URL` overrides the endpoint. |
| **Code-Point Open** (Ordnance Survey, `api.os.uk/downloads`) | Every UK postcode with a position (about 1.7 million), held locally | None expected for this open product; `WA_CODEPOINT_URL` can carry a key if OS asks for one | About 25 MB, published quarterly. Checked daily, downloaded when the table is empty or a month old. Open Government Licence with OS and Royal Mail attribution (below). Not verified against the live service from where this was written: see [docs/postcodes.md](postcodes.md). |
| **Boundary-Line** (Ordnance Survey, `api.os.uk/downloads`) | Regions, counties and districts or boroughs (wards, parishes and constituencies if asked for), simplified, held locally | None expected for this open product | A large download, published in May and October, checked daily and reloaded only when missing or about five months old. Open Government Licence v3.0. `WA_BOUNDARY_KINDS` chooses the kinds; see [docs/boundaries.md](boundaries.md). Not yet verified against the live download. |
| **Open USRN** (Ordnance Survey, `api.os.uk/downloads`) | Every street's reference number (USRN) and line, held locally, **off unless `WA_USRN_POLL_ENABLED=true`** | None expected | A million and more streets. Reloaded when missing or a year old. Open Government Licence v3.0. Not yet verified against the live download. |
| **Bus Open Data Service** (DfT, `data.bus-data.dft.gov.uk`) | Live bus positions (SIRI-VM) in a box around a place | Free key (secret `bods_api_key`), sent as `api_key`; register on the BODS site | Not stored; cached 10 seconds per area. Open Government Licence. Not verified against the live service from where this was written: see [docs/live-buses.md](live-buses.md). |
| **Transport for West Midlands** (`api.tfwm.org.uk`) | GTFS-Realtime trip updates: predicted times for every vehicle in the region | Free key from api-portal.tfwm.org.uk: `tfwm_app_id` and `tfwm_app_key`, sent as query parameters, over HTTPS | Not stored. Open Government Licence. Probe only so far (`python -m app.integrations.tfwm.probe`); not verified against the live service. |
| **postcodes.io** | Postcode to coordinates when you add a place, for any postcode the local table does not know | None | Public service; only asked for a postcode Code-Point Open lacks (new ones appear between quarterly releases) or before the table has loaded. Give coordinates instead if you would rather not. |
| **NaPTAN** (DfT, `naptan.api.dft.gov.uk`) | Every bus stop, station and ferry terminal in Great Britain, with position | None | About 100 MB CSV, refreshed weekly (checked daily). Open Government Licence v3.0. See [docs/naptan.md](naptan.md). |
| **Octopus Energy REST API** (`api.octopus.energy`) | Unit rates for your electricity tariff; the account endpoint to find the tariff | Rates: none. Account: your API key (`octopus_api_key`) | Rates update through the day; tomorrow's appear mid-afternoon. See [docs/energy.md](energy.md). |
| **Octopus GraphQL API** (`api.backend.octopus.energy`) | Saving Sessions and whether you joined | Your API key (`octopus_api_key`) | Unofficial interface taken from the BottlecapDave Home Assistant integration; see [docs/energy.md](energy.md). |
| **TfL Unified API: road disruptions** (`api.tfl.gov.uk/Road/all/Disruption`) | Current and planned works, closures and incidents on London roads, with their position | Optional app key (`tfl_app_key`) | Fetched every 15 minutes. Powered by TfL Open Data. See [docs/tfl-roads.md](tfl-roads.md). |
| **Environment Agency flood monitoring** (`environment.data.gov.uk/flood-monitoring`) | Current flood alerts and warnings (England) and the boundary of each warned area | None | List updated every 15 minutes. Open Government Licence v3.0. No service guarantee; not a substitute for official warnings. See [docs/floods.md](floods.md). |
| **OpenStreetMap** via Geofabrik extract | Map data for Valhalla | Download | Open Database Licence: credit "© OpenStreetMap contributors" wherever routes are shown to others. |

## Credits and licences

This project only uses these services through their public or registered-consumer interfaces and is not
affiliated with, or endorsed by, any of them. Where a provider asks for a credit, the pages show it.

- **Polleninformation:** "Austrian Pollen Information Service, www.polleninformation.at". Non-commercial use
  only, and a hard limit of 40 requests a day.
- **Skiddle and Ticketmaster:** events link back to their pages; Skiddle results show "Source: Skiddle".
- **Transport for London:** powered by TfL Open Data (Open Government Licence v3.0 and TfL's terms).
- **Fuel Finder and National Highways:** public-sector data under the Open Government Licence where stated
  by the provider; the raw Fuel Finder feed is not redistributed.
- **Environment Agency:** "This uses Environment Agency flood and river level data from the real-time data
  API (Beta)." Shown in the flood popups on the map.
- **Ordnance Survey (Boundary-Line, Open USRN):** "Contains OS data © Crown copyright and database right 2026." Open Government Licence v3.0; the licence file inside each download is the authority.
- **Ordnance Survey (Code-Point Open):** "Contains OS data © Crown copyright and database right 2026.
  Contains Royal Mail data © Royal Mail copyright and database right 2026. Contains National Statistics
  data © Crown copyright and database right 2026." The licence file inside the download is the authority;
  use its wording if it differs.
- **Bus Open Data Service:** contains public sector information licensed under the Open Government Licence v3.0.
- **NaPTAN:** Department for Transport, Open Government Licence v3.0.
- **OpenStreetMap:** © OpenStreetMap contributors (Open Database Licence), via Geofabrik extracts.
- **National Rail:** Darwin data through the Rail Data Marketplace, under its consumer terms.

If you run this yourself you agree to each provider's terms with your own key. Do not use it commercially
with a personal or non-commercial key, and keep within each provider's rate limits (the defaults do).

## Fuel Finder specifics

- Price units are pence per litre; values under 10 are treated as pounds and converted.
- Fuel codes: `E10` (standard petrol), `E5` (super), `B7_STANDARD` (diesel), `B7_PREMIUM`,
  `B10`, `HVO`.
- A response is a bare JSON list of up to 500 records per batch; a 404 on a batch number ends
  the pull; an empty 200 is a transient failure and is retried.
- A few stations have wrong coordinates. Anything outside a UK bounding box is flagged
  (`coords_ok = false`) and left out of queries.

## Not used yet (candidates)

- Live-traffic journey times from a routing provider (terms for personal use unchecked).
- Event calendars (race meetings, concerts) to predict heavy traffic.
- DVSA MOT history and DVLA vehicle enquiry (access is application-based).
- National Highways WebTRIS traffic counts.

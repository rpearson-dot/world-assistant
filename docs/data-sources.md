# Data sources

Check each provider's current terms before relying on this; the notes below are a summary,
not legal advice.

| Source | Used for | Access | Limits and notes |
|--------|----------|--------|------------------|
| **National Highways** (DATEX II v3.4 JSON, `api.data.nationalhighways.co.uk`) | Closures, speed managed areas, message signs | Subscription key, header `Ocp-Apim-Subscription-Key` | 10 calls a minute per key (the app uses 8). Closures and speed have no area filter, so they are fetched network-wide and filtered locally. Signs accept a bounding box. Speeds are in km/h. Check the provider's terms for reuse. |
| **Fuel Finder** (GOV.UK, `fuel-finder.service.gov.uk`) | Station details and fuel prices | OAuth client credentials | UK IP addresses only. 100 requests a minute, one at a time. Repeated failed logins cause a 5 minute lockout. Open Government Licence v3.0. The guidelines ask that the raw feed is not passed on, so this project stores it privately and exposes only derived answers. |
| **Transport for London** (Unified API, `api.tfl.gov.uk`) | Departures at a station or stop (`StopPoint/{id}/ArrivalDepartures`), line status | Optional free app key (`app_key`) | Works without a key at a low rate. Departures are cached for 30 seconds. Times with no zone are read as UK local time (`WA_TFL_NAIVE_TZ`). Check TfL's terms for reuse. |
| **Skiddle** (`skiddle.com/api/v1`) | Events near a place: gigs, club nights, comedy, theatre, sport and more | Free API key (secret `skiddle_api_key`), sent as `api_key` | Not stored: results are cached for 10 minutes. No numeric rate limit is published, so calls are limited to 30 a minute. Skiddle publishes no terms of use for the API that we found; read them when you apply for the key and keep the "Source: Skiddle" credit and the link to each event. Their web-api notes were archived in April 2024. `python -m app.integrations.skiddle.probe LAT LON` shows the real response. |
| **Polleninformation** (`polleninformation.at/api/forecast/public`) | Pollen by allergen (birch, grasses and so on) and allergy risk, today plus three days, for any saved place; set `WA_POLLEN_COUNTRY` (default `GB`) | Free personal API key (secret `pollen_api_key`), requested through the contact form on polleninformation.at/en/data-interface; sent as `apikey` | Cached 4 hours per location (their fair-use guidance is one fetch every 4 hours). A worker job also fetches a lattice of about 51 points across Britain three times a day (10 s apart; table `pollen_grid`) for Home Assistant's pollen map and nearest-point lookups. A worker job fetches for every saved place every 4 hours and keeps one value per place, day and pollen for 90 days (table `pollen_reading`, included in backups) for the graphs on `/pollen`. **Non-commercial use only**, and public apps must credit "Austrian Pollen Information Service, www.polleninformation.at" (shown on `/pollen`). `python -m app.integrations.pollen.probe LAT LON` shows the real response; `scripts/pollen-survey.py` measures how finely the forecast varies across Britain. |
| **Ticketmaster** (Discovery API v2, `app.ticketmaster.com/discovery/v2`) | Concerts, sport, theatre and shows near a place | Free consumer key (secret `ticketmaster_api_key`; the consumer secret is not needed), sent as `apikey` | Default quota 5,000 calls a day and 5 a second; we allow 240 a minute and cache results for 10 minutes. Not stored. Read their API terms of use and keep the link to each event. Types with no Ticketmaster equivalent (club nights, pubs) are not searched there. `python -m app.integrations.ticketmaster.probe LAT LON` shows the real response. |
| **National Rail** (Darwin, via the Rail Data Marketplace Live Departure Board) | Live departures for any station in Great Britain, by three-letter code (e.g. `SNL`) | Marketplace consumer key (secret `nr_access_token`), sent as `x-apikey` | Cached for 30 seconds. No station-name search on the marketplace. `WA_NR_BASE_URL` overrides the endpoint. |
| **postcodes.io** | Postcode to coordinates when you add a place | None | Public service; the postcode you type is sent to it. Give coordinates instead if you would rather not. |
| **OpenStreetMap** via Geofabrik extract | Map data for Valhalla | Download | Open Database Licence: credit "© OpenStreetMap contributors" wherever routes are shown to others. |

## Credits and licences

This project only uses these services through their public or registered-consumer interfaces and is not
affiliated with, or endorsed by, any of them. Where a provider asks for a credit, the pages show it.

- **Polleninformation:** "Austrian Pollen Information Service, www.polleninformation.at". Non-commercial use
  only, fair use of one fetch per location every four hours.
- **Skiddle and Ticketmaster:** events link back to their pages; Skiddle results show "Source: Skiddle".
- **Transport for London:** powered by TfL Open Data (Open Government Licence v3.0 and TfL's terms).
- **Fuel Finder and National Highways:** public-sector data under the Open Government Licence where stated
  by the provider; the raw Fuel Finder feed is not redistributed.
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

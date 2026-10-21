# Train departures

A board is a station plus an optional destination filter. Each board reads from one source:

| `source` | Covers | Station is | Needs |
| --- | --- | --- | --- |
| `tfl` | London (TfL Unified API) | StopPoint id, e.g. `910GWORCSPK`, plus line ids | optional TfL app key |
| `nationalrail` | anywhere in Great Britain (Darwin, via the Rail Data Marketplace) | three-letter code, e.g. `SNL` | Marketplace consumer key |

Use `nationalrail` for a station TfL does not cover, or if you want the same data a National Rail
Home Assistant integration shows. Use `tfl` for TfL's own view of London services.

## Set up National Rail

The data comes from the Rail Data Marketplace's Live Departure Board.

1. Register a consumer account on the Rail Data Marketplace and subscribe to the
   **Live Departure Board** product. Copy the **consumer key** (you do not need the secret).
2. Save the key, on its own line and nothing else, in `nr_access_token.txt` in your secrets folder.
3. Rebuild and restart: `docker compose up -d --build`.
4. Check it works: `docker compose exec api python -m app.integrations.rail.probe departures SNL`.
   It prints field names and one sample service. Never paste the key anywhere.
5. Open `/trains`, choose National Rail, type the three-letter station code (for example `SNL`)
   in the search box, press Find, then add a board. The marketplace has no station-name search.

Destination filters (`include` / `exclude`) work on the destination name, e.g. include `Waterloo`.

## Showing departures by platform

On `/trains`, the box above the boards ("Show by platform") takes labels such as
`1=To London, 2=From London`. Press Apply and each board groups its rows under those headings,
with anything else under "Other or not yet known". This only changes how the page looks, and it is
remembered in your browser. The API and the data are unchanged.

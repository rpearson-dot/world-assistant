# Fuel Finder (GOV.UK) (`fuel`)

Prices at every UK fuel station, cheapest near you or on the way.

The API only answers UK addresses.

How to use it: [docs/data-sources.md](../../../docs/data-sources.md).

| | |
|---|---|
| Keys | `fuel_finder_client_id`, `fuel_finder_client_secret` |
| Uses when there | [nh](../nh/), [vehicles](../vehicles/) |
| Endpoints | `/v1/fuel`, `/fuel` |
| Scheduled jobs | `fuel_sync_prices`, `fuel_sync_stations` |
| Assistant tools | `fuel_cheapest` |
| Plugs into | `assistant.tools`, `fuel.canonical` |

## Files

- `fuel.py`: Fuel price API: cheapest near a place, a point or along a corridor.
- `sources/fuelfinder/capture.py`: Manual capture of raw Fuel Finder responses, to learn the real JSON shape.
- `sources/fuelfinder/client.py`: Client for the UK government Fuel Finder API.
- `sources/fuelfinder/normalise.py`: Value cleaning for Fuel Finder data. Pure functions, no I/O.
- `sources/fuelfinder/queries.py`: Find the cheapest fuel near a point or along a corridor.
- `sources/fuelfinder/store.py`: Postgres storage for Fuel Finder stations and prices.
- `sources/fuelfinder/sync.py`: Fuel Finder sync: pull from the API (or a stored capture) into Postgres.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

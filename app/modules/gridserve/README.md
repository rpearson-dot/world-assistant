# GRIDSERVE EV chargers (`gridserve`)

GRIDSERVE's chargers near your places and along your trips: how many work and are free now, by connector, and the price.

Open data under the Public Charge Point Regulations 2023.

How to use it: [docs/ev.md](../../../docs/ev.md).

| | |
|---|---|
| Keys | `gridserve_api_key` |
| Uses when there | [conditions](../conditions/) |
| Endpoints | `/v1/ev` |
| Scheduled jobs | `gridserve_sync` |
| Assistant tools | `ev_chargers` |
| Plugs into | `assistant.tools`, `map.ev` |

## Files

- `ev.py`: EV chargers: sites near a place or along a trip, how many chargers work and are free, by connector, and prices.
- `map_layer.py`: The map's EV charger layer, drawn by this module and plugged in through the registry ("map.ev").
- `sources/gridserve/client.py`: GRIDSERVE's open data API (published under the Public Charge Point Regulations 2023).
- `sources/gridserve/normalise.py`: OCPI locations and tariffs made into rows, and a site's chargers summed up. Pure.
- `sources/gridserve/probe.py`: What GRIDSERVE's API sends, and how this app reads it. Prints no key.
- `sources/gridserve/sync.py`: GRIDSERVE: read every site, its chargers and their status, and the prices, on a schedule.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

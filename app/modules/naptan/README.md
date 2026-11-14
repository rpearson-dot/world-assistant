# Bus stops and stations (NaPTAN) (`naptan`)

Every stop and station in Great Britain.

About 100 MB, loaded on first start.

How to use it: [docs/naptan.md](../../../docs/naptan.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | NaPTAN: every stop and station (a download) |
| Endpoints | `/v1/stops` |
| Scheduled jobs | `naptan_sync` |
| Assistant tools | `stops_near` |
| Plugs into | `assistant.tools`, `naptan.station_types` |

## Files

- `sources/naptan/normalise.py`: NaPTAN rows, as the national CSV gives them, into what we store.
- `sources/naptan/sync.py`: NaPTAN sync: download the national register and keep it in Postgres.
- `stops.py`: Bus stops, stations and terminals, from the NaPTAN register held in Postgres.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

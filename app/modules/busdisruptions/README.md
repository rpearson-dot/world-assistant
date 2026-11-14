# Bus disruptions (BODS) (`busdisruptions`)

Bus, tram and ferry diversions and closures near your places, where the local authority publishes them.

Only some authorities publish (none in London or the Midlands yet); off unless WA_BUS_DISRUPTIONS_POLL_ENABLED=true.

How to use it: [docs/bus-disruptions.md](../../../docs/bus-disruptions.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | Bus Open Data Service disruptions |
| Keys | `bods_api_key` |
| Needs | [bods](../bods/) |
| Uses when there | [bus](../bus/) |
| Endpoints | `/v1/bus-disruptions` |
| Scheduled jobs | `bus_disruptions_poll` |
| Assistant tools | `bus_disruptions` |
| Plugs into | `assistant.tools` |

## Files

- `bus_disruptions.py`: Bus, tram and ferry disruptions near your places, from the authorities that publish them to BODS.
- `disruptions_sync.py`: Keep the current bus disruptions (BODS SIRI-SX), every 10 minutes.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

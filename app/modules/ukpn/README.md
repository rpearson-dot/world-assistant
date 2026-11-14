# UK Power Networks (`ukpn`)

Power cuts and electricity street works near your places, and the substations feeding them (London, South East and East of England).

Reads nothing unless a saved place is on their network. CC BY 4.0.

How to use it: [docs/power.md](../../../docs/power.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | UK Power Networks open data: power cuts and street works |
| Gives | Home Assistant: power cuts and works |
| Keys | `ukpn_api_key` (optional) |
| Endpoints | `/v1/power` |
| Scheduled jobs | `ukpn_places`, `ukpn_faults`, `ukpn_works_open`, `ukpn_works_proposed` |
| Assistant tools | `power` |
| Plugs into | `assistant.tools` |

## Files

- `power.py`: Power: cuts and works near your places, and what supplies them (UK Power Networks).
- `sources/ukpn/client.py`: UK Power Networks open data portal (Opendatasoft, API v2.1).
- `sources/ukpn/normalise.py`: UK Power Networks records made into power cuts, works and areas. Pure.
- `sources/ukpn/probe.py`: Show what the UK Power Networks portal has for each dataset this app uses, and what this app makes of it.
- `sources/ukpn/sync.py`: UK Power Networks: read the portal on a schedule and keep what is about your places.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

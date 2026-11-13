# Trip conditions (`conditions`)

Road works, closures and floods on a saved trip's route.

How to use it: [docs/trip-conditions.md](../../../docs/trip-conditions.md).

| | |
|---|---|
| Uses when there | [tflroads](../tflroads/) |
| Endpoints | `/v1/conditions`, `re:/v1/trips/[^/]+/conditions` |
| Assistant tools | `road_conditions` |
| Plugs into | `assistant.tools` |

## Files

- `conditions.py`: What is on the way: road works, closures, incidents and flood warnings along a trip's route, now and planned.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

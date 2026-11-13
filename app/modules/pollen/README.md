# Polleninformation (`pollen`)

Pollen by allergen, today and three days ahead.

Free for personal use; 40 requests a day.

How to use it: [docs/environment.md](../../../docs/environment.md).

| | |
|---|---|
| Keys | `pollen_api_key` |
| Endpoints | `/v1/pollen`, `/pollen` |
| Scheduled jobs | `pollen_sync`, `pollen_grid_sync` |
| Assistant tools | `pollen` |
| Plugs into | `assistant.tools`, `ha.pollen_map` |

## Files

- `pollen.py`: Pollen and allergy risk near a saved place: Polleninformation forecast, cached for the day.
- `pollen_budget.py`: A hard daily budget for calls to the pollen service.
- `pollen_grid.py`: A nationwide pollen forecast, fetched on a lattice and kept here.
- `pollen_history.py`: A running history of pollen readings: one value per place, day and allergen, kept 90 days.
- `pollen_map.py`: A map of Britain coloured by allergy risk, drawn as SVG from the stored pollen lattice.
- `sources/pollen/client.py`: Pollen forecast from the Austrian Pollen Information Service (polleninformation.at).
- `sources/pollen/probe.py`: Look at a real pollen response while setting up (needs the pollen_api_key secret).
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

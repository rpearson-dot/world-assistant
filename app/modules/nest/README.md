# Nest thermostat (`nest`)

Your Nest's room temperature, humidity, target and whether it is calling for heat, read through Google's Smart Device Management API and kept — read-only, for the heating picture.

A one-time Google Device Access setup ($5) mints the four keys for the Keys page. Tracking only, no control

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | the Nest thermostat (Google Smart Device Management API) |
| Gives | Home Assistant: room temperature, humidity, target and a heating binary sensor |
| Keys | `nest_project_id`, `nest_client_id`, `nest_client_secret`, `nest_refresh_token` |
| Endpoints | `/v1/nest`, `/nest` |
| Scheduled jobs | `nest_sync` |
| Assistant tools | `nest_status` |
| Plugs into | `assistant.tools` |

## Files

- `__init__.py`: Nest thermostat, read-only, through Google's Smart Device Management API.
- `api.py`: The Nest thermostat (/v1/nest, the In the home → Heating tab): room temperature, humidity, the target, the mode and whether it is calling for heat, read...
- `page.py`: The Nest page (/nest, In the home → Heating): each thermostat's room temperature, target, humidity, mode and whether it is calling for heat, with a history...
- `sdm.py`: Google Smart Device Management (SDM): reading a Nest thermostat.
- `tools.py`: The assistant's tool for this module, served over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

# Heat pump (Ecodan) (`ecodan`)

Your Mitsubishi Ecodan read through MELCloud: hot-water tank temperature and target, the boost, flow/return and outdoor temperatures, the heating zone, power and daily energy — read-only for now (PV-to-hot-water and tariff-aligned hot water come next, shadow-first).

MELCloud throttles hard, so it is read every 10 minutes. Tracking only; see claude/ecodan-design.md

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | the Mitsubishi Ecodan heat pump (MELCloud) |
| Gives | Home Assistant: tank temperature and target, flow/return and outdoor temperatures, and a hot-water-boost sensor |
| Keys | `melcloud_email`, `melcloud_password` |
| Endpoints | `/v1/ecodan`, `/ecodan` |
| Scheduled jobs | `ecodan_sync` |
| Assistant tools | `ecodan_status` |
| Plugs into | `assistant.tools` |

## Files

- `__init__.py`: Mitsubishi Ecodan heat pump, read-only, through MELCloud.
- `api.py`: The Ecodan heat pump (/v1/ecodan, the Energy → Heat pump tab): tank temperature and target, the hot-water boost state, flow/return and outdoor temperatures,...
- `melcloud.py`: MELCloud: reading a Mitsubishi Ecodan air-to-water (ATW) heat pump.
- `page.py`: The Ecodan page (/ecodan, Energy → Heat pump): each heat pump's tank temperature and target, the hot-water boost, flow/return and outdoor temperatures, the...
- `tools.py`: The assistant's tool for this module, served over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

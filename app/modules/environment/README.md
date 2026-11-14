# Air and water (`environment`)

Air quality (Defra's 1-10 index), river levels, rainfall and tide gauges near your places.

Live from Defra UK-AIR and the Environment Agency; no key. Rivers, rain and tides: England only.

How to use it: [docs/environment.md](../../../docs/environment.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | Defra air quality; Environment Agency river, rain and tide gauges; NRFA river flows; storm overflows (Thames Water) |
| Gives | Home Assistant: air, rivers and tides; notifications: environment warnings |
| Uses when there | [pollen](../pollen/) |
| Endpoints | `/v1/environment`, `/environment` |
| Scheduled jobs | `environment_alerts` |
| Assistant tools | `air_quality`, `river_levels`, `tides`, `storm_overflows`, `watch_rivers` |
| Plugs into | `assistant.tools` |

## Files

- `environment.py`: Air and water near your places: air quality (Defra's 1-10 index), river levels, rainfall and tide gauges.
- `environment_page.py`: The Environment page: air quality, pollen, floods and rivers, tides and storm overflows for each of your places, a tab each (Pollen is its own page,...
- `notify.py`: Notifications from the environment, every half hour: a flood warning at one of your places (severe ones even in quiet hours), the air quality index high (7...
- `sources/environment/clients.py`: Air quality (Defra UK-AIR) and river levels, rainfall and tide gauges (Environment Agency), read live.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

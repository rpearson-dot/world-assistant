# Weather (Open-Meteo) (`weather`)

The weather at home: now, hour by hour for a week, each day's heating degree-hours, and the past as it turned out, for heating and solar planning.

No key. Free for non-commercial use; data CC BY 4.0

How to use it: [docs/weather.md](../../../docs/weather.md).

| | |
|---|---|
| Endpoints | `/v1/weather`, `/weather` |
| Scheduled jobs | `weather_sync` |
| Assistant tools | `weather` |
| Plugs into | `assistant.tools` |

## Files

- `api.py`: Weather (/v1/weather) for your home place from Open-Meteo: now, the next hours, each day in brief with its heating degree-hours, and the past as it turned...
- `meteo.py`: Open-Meteo: the hourly forecast (api.open-meteo.com) and past weather (archive-api.open-meteo.com), no key. Free for non-commercial use; data CC BY 4.0,...
- `page.py`: The weather page (/weather, a tab of Environment): now, the next 48 hours, each day ahead with its heating degree-hours, and the last month as it turned...
- `tools.py`: The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

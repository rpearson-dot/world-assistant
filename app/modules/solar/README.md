# Solar forecast (forecast.solar) (`solar`)

How much your solar panels should make today and tomorrow, hour by hour, refreshed every hour, and how far off it has been against what they really made.

No key: 12 requests an hour from one address, one for each array. The accuracy check reads your panels' output from Home Assistant

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | forecast.solar; Home Assistant: the panels' output statistic (accuracy check); Home Assistant: Forecast.Solar arrays (copying, through the share) |
| Gives | Home Assistant: solar expected today, still to come, tomorrow, power now |
| Endpoints | `/v1/solar`, `/solar` |
| Scheduled jobs | `solar_sync` |
| Assistant tools | `solar_forecast` |
| Plugs into | `assistant.tools` |

## Files

- `api.py`: Solar forecast (/v1/solar, the Energy → Solar tab): forecast.solar's expected output of your arrays for today and tomorrow, refreshed each hour; each day's...
- `fsolar.py`: forecast.solar: how much your solar panels should make, for each array (a "plane": one tilt, one direction, one size), today and tomorrow, by the quarter or...
- `page.py`: The solar forecast page (/solar, a tab of Energy): today's and tomorrow's expected output hour by hour, how far off the forecast has been, and the arrays....
- `tools.py`: The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

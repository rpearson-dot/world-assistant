# Home energy planner (`planner`)

When to charge the car (cheap rate, free hours, solar to spare), decided every five minutes beside Home Assistant's plan, and how predictable evening demand is from the weather.

In shadow until you turn on "act": it decides and keeps each decision, and only starts or stops the charger when told to (Auto, with the charger on World Assistant's OCPP). Reads Home Assistant

| | |
|---|---|
| Role | Processor: works from other data |
| Reads | Home Assistant: the house's use statistic, the plan, the charger and car sensors, the battery and grid entities |
| Gives | notifications: the planner's month |
| **Controls** | the car charger: start and stop, only with "act" ticked and the switch on Auto |
| Uses when there | [octopus](../octopus/), [weather](../weather/), [ocpp](../ocpp/), [ovms](../ovms/), [solar](../solar/) |
| Endpoints | `/v1/planner`, `/planner` |
| Scheduled jobs | `planner_tick`, `demand_job`, `planner_review` |
| Assistant tools | `charging_plan`, `evening_demand` |
| Plugs into | `assistant.tools` |

## Files

- `api.py`: The home energy planner (/v1/planner, the Energy → Planner tab).
- `charge.py`: When to charge the car: the decision World Assistant makes, in shadow beside Home Assistant's plan until it is trusted. Pure: given what is true now, the...
- `demand.py`: How predictable the house's evening demand is from the weather. Pure: given each day's evening kWh (16:00 to 19:00 by default) and the same evening's...
- `hass.py`: Home Assistant reading for the planner: the core's (app.ha_stats), kept under this name for the planner's code.
- `page.py`: The planner page (/planner, a tab of Energy): what World Assistant would do with the car now and why, how often that matched Home Assistant's plan, each...
- `review.py`: The planner's month against what really happened: each month, what charging the car really cost and what it would have cost had the planner been in charge,...
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

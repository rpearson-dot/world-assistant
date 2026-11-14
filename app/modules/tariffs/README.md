# Tariff comparison (`tariffs`)

Your real half-hourly use priced at every Octopus tariff's rates today (and any other supplier's you add from a quote), as used and with your house battery moving use into each tariff's cheap windows, ranked, with the best import and export to move to.

Worked out only when you press Work it out again on Energy → Tariffs. Needs the Octopus module's meter readings (the account number and API key). Octopus's tariffs are public; other suppliers' are typed in by hand

| | |
|---|---|
| Role | Processor: works from other data |
| Reads | Octopus Energy's public tariffs |
| Uses when there | [octopus](../octopus/) |
| Endpoints | `/v1/tariffs`, `/tariffs` |
| Assistant tools | `best_tariff` |
| Plugs into | `assistant.tools` |

## Files

- `api.py`: Tariff comparison (/v1/tariffs, the Energy → Tariffs tab): your real half-hourly consumption, from the Octopus module's meter readings, priced at each...
- `catalogue.py`: Octopus Energy's current tariffs for your region, from its public products API (no key): each product open to new customers (not business, not prepay, not...
- `page.py`: The tariffs page (/tariffs, a tab of Energy): the best tariff to move to, every tariff ranked on your own use at today's rates (as used, and with your...
- `price.py`: Pricing your real consumption at each tariff's rates today. Pure.
- `tools.py`: The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

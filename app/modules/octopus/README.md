# Octopus Energy (`octopus`)

Your electricity rates, cheap windows and Saving Sessions.

Rates also work with just a tariff code (WA_OCTOPUS_TARIFF).

How to use it: [docs/energy.md](../../../docs/energy.md).

| | |
|---|---|
| Keys | `octopus_api_key` |
| Endpoints | `/v1/energy`, `/energy` |
| Scheduled jobs | `energy_sync`, `energy_usage` |
| Assistant tools | `electricity` |
| Plugs into | `assistant.tools`, `ha.energy_device`, `ha.energy_retired` |

## Files

- `__init__.py`: Energy supply: what electricity costs now and later, for Home Assistant to act on.
- `ha.py`: The Home Assistant "Energy" device built from /v1/energy/now and /v1/energy/rates.
- `kraken.py`: Octopus's GraphQL API (Kraken): Saving Sessions.
- `octopus.py`: Octopus Energy's public REST API: which tariff an account is on, and that tariff's unit rates.
- `page.py`: The Energy page: what electricity costs now and over the next two days, and Octopus's sessions.
- `probe.py`: Show what Octopus really sends for Saving Sessions, so the parser can be checked.
- `rates.py`: What the rates mean: bands, windows and the cheapest block. Pure, so every rule is testable alone.
- `routes.py`: Electricity prices for Home Assistant (and anything else) to act on. Read-only: nothing here controls a device.
- `schedule.py`: The inverter's six time slots, worked out from the tariff. Pure.
- `sessions.py`: Saving Sessions (and boost events) as Home Assistant wants them: on now, and what is next. Pure.
- `speech.py`: The Energy answer as a sentence for the assistant (voice, Siri, MCP). Pure.
- `store.py`: Rates in Postgres, and the answers worked out from them.
- `summary.py`: The answer Home Assistant wants: now, next, and the windows ahead. Pure over a list of periods.
- `sync.py`: Fetch the tariff's unit rates and store them. Runs every 30 minutes; tomorrow's rates appear mid-afternoon.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).
- `usage.py`: Your electricity meter's half-hourly readings from Octopus (import, and export where there is an export meter), kept so every plan can be checked against...

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

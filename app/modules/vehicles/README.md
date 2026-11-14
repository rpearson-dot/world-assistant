# Vehicles and trip cost (`vehicles`)

What a trip costs in fuel or electricity for a saved vehicle.

How to use it: [docs/leaf.md](../../../docs/leaf.md).

| | |
|---|---|
| Role | Processor: works from other data |
| Uses when there | [fuel](../fuel/), [gridserve](../gridserve/), [nh](../nh/), [octopus](../octopus/) |
| Endpoints | `/v1/vehicles`, `/vehicles`, `re:/v1/trips/[^/]+/cost` |
| Assistant tools | `trip_cost` |
| Plugs into | `assistant.tools` |

## Files

- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).
- `vehicles.py`: Vehicles, trip running cost, and detour maths (is a cheaper station worth the trip?).
- `vehicles_page.py`: The Vehicles page: the cars you drive, what they run on and how far they go on it.

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

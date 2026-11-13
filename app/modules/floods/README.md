# Environment Agency (`floods`)

Flood alerts and warnings in England, on the map and on your routes.

How to use it: [docs/floods.md](../../../docs/floods.md).

| | |
|---|---|
| Endpoints | `/v1/floods` |
| Scheduled jobs | `flood_poll` |
| Assistant tools | `floods_near` |
| Plugs into | `assistant.tools` |

## Files

- `floods.py`: Flood warnings and alerts from the Environment Agency (England).
- `sources/flood/normalise.py`: Environment Agency flood-warning items into what we store. Pure, so the rules are testable alone.
- `sources/flood/sync.py`: Environment Agency flood warnings: the current list, and the boundary of any area not yet held.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

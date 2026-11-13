# TfL road disruptions (`tflroads`)

London works, closures and incidents near your routes.

How to use it: [docs/tfl-roads.md](../../../docs/tfl-roads.md).

| | |
|---|---|
| Uses when there | [tfl](../tfl/) |
| Endpoints | `/v1/roads/tfl` |
| Scheduled jobs | `tflroads_poll` |
| Assistant tools | `road_disruptions_near` |
| Plugs into | `assistant.tools` |

## Files

- `sources/tflroads/normalise.py`: TfL road disruptions into what we store. Pure, so every rule is testable alone.
- `sources/tflroads/probe.py`: Show what TfL really sends for road disruptions, and how much of it this app can use.
- `sources/tflroads/sync.py`: TfL road disruptions: the full London list, every 15 minutes.
- `tflroads.py`: London road disruptions from TfL: works, closures, collisions and incidents, with where they are.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

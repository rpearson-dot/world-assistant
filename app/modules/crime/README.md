# Crime and police (`crime`)

Street-level crime within a mile of a place and its neighbourhood police team (data.police.uk), looked up when asked.

England, Wales and Northern Ireland; the figures are about two months behind.

How to use it: [docs/data-sources.md](../../../docs/data-sources.md).

| | |
|---|---|
| Uses when there | [codepoint](../codepoint/) |
| Endpoints | `/v1/crime` |
| Assistant tools | `crime` |
| Plugs into | `assistant.tools` |

## Files

- `crime.py`: Crime and the local police team for a place, from data.police.uk (no key; Open Government Licence v3.0).
- `crime_areas.py`: Crime in the borough around a point compared with the boroughs next to it: recorded crimes over the latest three published months (data.police.uk, counted...
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

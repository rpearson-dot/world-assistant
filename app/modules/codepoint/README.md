# Postcodes (OS Code-Point Open) (`codepoint`)

Places by postcode without an outside service.

About 25 MB, loaded on first start.

How to use it: [docs/postcodes.md](../../../docs/postcodes.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | OS Code-Point Open (a download) |
| Endpoints | `/v1/postcodes` |
| Scheduled jobs | `codepoint_sync` |
| Assistant tools | `postcode` |
| Plugs into | `assistant.tools`, `postcodes.local` |

## Files

- `postcodes.py`: Postcodes from the local Code-Point Open table: look one up, or find the nearest to a point.
- `sources/codepoint/sync.py`: Code-Point Open: every UK postcode with a position, loaded from Ordnance Survey's open download.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

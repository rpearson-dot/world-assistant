# Here (`here`)

Everything World Assistant knows about where you are now, without saving a place.

Works without a key; with what3words' (free plan) it shows the three words for a place and takes three words instead of a postcode.

How to use it: [docs/here.md](../../../docs/here.md).

| | |
|---|---|
| Keys | `what3words_api_key` (optional) |
| Uses when there | [codepoint](../codepoint/) |
| Endpoints | `/v1/here`, `/here` |
| Assistant tools | `here` |
| Plugs into | `assistant.tools` |

## Files

- `here.py`: Here: what World Assistant knows about wherever you are, without saving it as a place first.
- `here_page.py`: The Here page: what World Assistant knows about wherever you are, from the browser's location or a postcode.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

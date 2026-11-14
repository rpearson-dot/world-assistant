# Outdoors (`outdoors`)

A KitchenOwl list of outdoor gear, priced every day at GO Outdoors and Decathlon, with price drops.

Add a KitchenOwl list called "Outdoors". GO Outdoors is searched by name; Decathlon only by a pasted link

How to use it: [docs/outdoors.md](../../../docs/outdoors.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | GO Outdoors, Decathlon and other shops' prices |
| Gives | Home Assistant: outdoor gear prices |
| Keys | `kitchenowl_token` |
| Needs | [shopping](../shopping/) |
| Uses when there | [wishlist](../wishlist/) |
| Endpoints | `/v1/outdoors`, `/outdoors` |
| Scheduled jobs | `outdoors_check` |
| Assistant tools | `outdoors`, `outdoors_add`, `outdoors_check` |
| Plugs into | `assistant.tools` |

## Files

- `outdoors.py`: Outdoors: a KitchenOwl list of outdoor gear, priced every day at GO Outdoors and Decathlon, with price drops.
- `outdoors_page.py`: The Outdoors page: the family's KitchenOwl "Outdoors" list, priced every day at GO Outdoors and Decathlon.
- `sources/outdoors/clients.py`: GO Outdoors, Decathlon and Bikesy prices, read for the Outdoors list.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

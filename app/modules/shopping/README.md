# Shopping (`shopping`)

The family's KitchenOwl list, priced at Sainsbury's and Ocado.

Needs docker-compose.kitchenowl.yml, and docker-compose.supermarket.yml for prices (docs/shopping.md)

How to use it: [docs/shopping.md](../../../docs/shopping.md).

| | |
|---|---|
| Keys | `kitchenowl_token` |
| Endpoints | `/v1/shopping`, `/shopping` |
| Assistant tools | `shopping`, `add_to_list`, `price_list`, `send_to_basket`, `orders`, `tick_off_order`, `clear_list` |
| Plugs into | `assistant.tools` |

## Files

- `shopping.py`: Shopping: the family's KitchenOwl list, priced at Sainsbury's and Ocado.
- `shopping_page.py`: The Shopping page: the family's KitchenOwl list, priced at Sainsbury's and Ocado, and the retailer logins.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

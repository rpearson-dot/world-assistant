# Food standards (`food`)

Food hygiene ratings near a place, and the FSA's allergy alerts and product recalls checked against your shopping list and usual products.

No key. Alerts are matched against the KitchenOwl list and usual products when shopping is set up.

How to use it: [docs/data-sources.md](../../../docs/data-sources.md).

| | |
|---|---|
| Uses when there | [crime](../crime/), [shopping](../shopping/), [wishlist](../wishlist/) |
| Endpoints | `/v1/food` |
| Scheduled jobs | `food_alerts_check` |
| Assistant tools | `food_hygiene`, `food_alerts` |
| Plugs into | `assistant.tools` |

## Files

- `food.py`: The Food Standards Agency: food hygiene ratings near a place, and food alerts (allergy alerts and product recalls) checked against your shopping (no key;...
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

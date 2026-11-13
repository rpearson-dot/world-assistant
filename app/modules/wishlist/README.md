# Wishlist (`wishlist`)

A KitchenOwl list of things to look out for in Aldi's Specialbuys and the Middle of Lidl.

Add a KitchenOwl list called "Wishlist". Reads Aldi's and Lidl's own website searches: unofficial, so it may need fixing when they change

How to use it: [docs/wishlist.md](../../../docs/wishlist.md).

| | |
|---|---|
| Keys | `kitchenowl_token` |
| Needs | [shopping](../shopping/) |
| Endpoints | `/v1/wishlist`, `/wishlist` |
| Scheduled jobs | `wishlist_check` |
| Assistant tools | `wishlist`, `wish_add`, `wish_remove`, `wishlist_check` |
| Plugs into | `assistant.tools` |

## Files

- `sources/wishlist/clients.py`: Aldi's and Lidl's own product search, read for the wishlist.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).
- `wishlist.py`: The wishlist: a KitchenOwl list of things to look out for in Aldi's Specialbuys and the Middle of Lidl.
- `wishlist_page.py`: The Wishlist page: the family's KitchenOwl "Wishlist" list, looked for in Aldi's Specialbuys and the Middle of Lidl, with when each was last seen.

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

# Pi-hole (`pihole`)

Every DNS lookup each device makes, your Pi-hole groups, and suggested blocks for what gets past your Pi-hole lists that does not serve you (added only when you press Add to Pi-hole, with Undo).

Pi-hole v6: its address on the Devices page, its app password (Settings → Web interface / API → Configure app password) here. Your groups are never changed. Nothing goes to Home Assistant

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | Pi-hole's query log, network table and groups (its v6 API, on the home network); HaGeZi's manufacturer tracker lists (to recognise what gets past Pi-hole) |
| Gives | each device's lookups, suggested blocks |
| **Controls** | Pi-hole: blocks the domains you tick, for one device or one of your groups, only when you press Add to Pi-hole (Undo takes them back) |
| Keys | `pihole_app_password` |
| Needs | [network](../network/) |
| Endpoints | `/v1/pihole`, `/pihole` |
| Scheduled jobs | `network_pihole`, `network_lists` |
| Assistant tools | `pihole_groups` |
| Plugs into | `assistant.tools` |

## Files

- `__init__.py`: Pi-hole: every DNS lookup each device makes, your Pi-hole groups, and suggested blocks for what gets past your Pi-hole lists that does not serve you (added...
- `api.py`: Pi-hole (/v1/pihole, Your network → Network): its lookups read each minute and put against each device, the devices it knows (so it works without the UniFi...
- `client.py`: Pi-hole v6's API on the home network: reading its query log, and (only when you press Add to Pi-hole) blocking domains for one device.
- `page.py`: The Pi-hole page (/pihole, In the home → Networking → Pi-hole): the DNS picture — which devices look up the most, what gets past your Pi-hole lists that...
- `suggest.py`: Suggested blocks for each device: what gets past your Pi-hole lists that does not serve you. Pure.
- `tools.py`: The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

# Network (UniFi and Pi-hole) (`network`)

Every device on your network and what it does: joining and leaving, its DNS lookups through Pi-hole, firewall decisions, and suggested blocks for what gets past your Pi-hole lists that does not serve you.

The UniFi gateway sends its syslog to this server (UDP 5514 unless WA_SYSLOG_PORT says otherwise); Pi-hole is read through its own API. Nothing goes to Home Assistant

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | the UniFi gateway's syslog (UDP, from the home network); Pi-hole's query log and groups (its v6 API, on the home network); HaGeZi's manufacturer tracker lists (to recognise what gets past Pi-hole) |
| Gives | notifications: a new device on the network |
| **Controls** | Pi-hole: blocks the domains you tick, for one device or one of your groups, only when you press Add to Pi-hole (Undo takes them back) |
| Keys | `pihole_app_password` (optional) |
| Endpoints | `/v1/network`, `/network` |
| Scheduled jobs | `network_pihole`, `network_lists`, `network_housekeep` |
| Assistant tools | `network_devices` |
| Plugs into | `assistant.tools` |

## Files

- `api.py`: The home network (/v1/network, In the home → Network): every device the router has seen, what each did (joining and leaving, DNS lookups through Pi-hole,...
- `listener.py`: The syslog listener (the wa-syslog service): receives what the UniFi gateway sends on UDP (port 5514 in the container; the host's port is WA_SYSLOG_PORT,...
- `page.py`: The network page (/network, In the home → Network): your devices, what each did, and suggested blocks for what gets past your Pi-hole lists. The page holds...
- `parse.py`: Reading what the UniFi gateway sends by syslog, and what Pi-hole's API answers. Pure.
- `pihole.py`: Pi-hole v6's API on the home network: reading its query log, and (only when you press Add to Pi-hole) blocking domains for one device.
- `store.py`: Keeping network events: each batch of events written, devices updated (by MAC address), the day's counts by device, and the first sighting of a device noted...
- `suggest.py`: Suggested blocks for each device: what gets past your Pi-hole lists that does not serve you. Pure.
- `tools.py`: The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

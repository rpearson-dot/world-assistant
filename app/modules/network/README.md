# Network devices (`network`)

Every device on your network and what it does, from the UniFi gateway log and Pi-hole (whichever you use): the page and the record they share.

Turned on with the UniFi gateway log, Pi-hole or Discovery. Nothing goes to Home Assistant

| | |
|---|---|
| Role | Processor: works from other data |
| Reads | what the UniFi gateway log and Pi-hole see |
| Gives | notifications: a new device on the network |
| Uses when there | [ocpp](../ocpp/) |
| Endpoints | `/v1/network`, `/network` |
| Scheduled jobs | `network_housekeep` |
| Assistant tools | `network_devices` |
| Plugs into | `assistant.tools` |

## Files

- `api.py`: Your network's devices (/v1/network, Your network → Network): every device seen by the UniFi gateway log or Pi-hole, and what each did (joining and leaving,...
- `firewall.py`: Advisory firewall-block suggestions for one device, from what the UniFi gateway logged it reaching. Pure.
- `listener.py`: The syslog listener (the wa-syslog service): receives what the UniFi gateway sends on UDP (port 5514 in the container; the host's port is WA_SYSLOG_PORT,...
- `page.py`: The network page (/network, In the home → Network): your devices, what each did, and suggested blocks for what gets past your Pi-hole lists. The page holds...
- `parse.py`: Reading what the UniFi gateway sends by syslog, and what Pi-hole's API answers. Pure.
- `store.py`: Keeping network events: each batch of events written, devices updated (by MAC address), the day's counts by device, and the first sighting of a device noted...
- `tools.py`: The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

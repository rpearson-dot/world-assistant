# Car charger (OCPP) (`ocpp`)

World Assistant as your charger's OCPP server: start and stop, every restart, drop and fault recorded, and the charger in Home Assistant.

Point the charger's OCPP 1.6J central system at ws://<this server>:8010/ocpp/<charge point ID> on your home network. It replaces Home Assistant's OCPP integration (a charger talks to one central system)

How to use it: [docs/ocpp.md](../../../docs/ocpp.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | the car charger (OCPP 1.6J, on the home network) |
| Gives | Home Assistant: charger sensors and switch; notifications: charger restarts, faults and drops |
| **Controls** | the car charger: start and stop charging, Off/On/Auto, availability |
| Endpoints | `/v1/ocpp`, `/charger`, `/ocpp` |
| Scheduled jobs | `ocpp_watch`, `ocpp_housekeep` |
| Assistant tools | `charger` |
| Plugs into | `assistant.tools`, `startup`, `shutdown` |

## Files

- `api.py`: The charger API (/v1/ocpp): how each charger stands, its settings, starting and stopping, other commands, and its record (events, messages, sessions). Plus...
- `ha.py`: The charger in Home Assistant, over MQTT: its status, power, energy and connection as sensors, and a "charging" switch that starts and stops a session...
- `page.py`: The Charger page (/charger): the charger's state now, start and stop, its restarts, drops and faults in a timeline, its sessions, its settings and its...
- `protocol.py`: OCPP 1.6J, the part a home charger needs, as pure functions: reading and writing frames, the central system's answer to each thing a charger says, and...
- `server.py`: World Assistant as the charger's central system: the charger connects to ws://<server>:8010/ocpp/<its id> and stays connected. Everything it says and...
- `store.py`: The charger's record (migration 0049). Messages, events and sessions are only added to; ocpp_charger holds how each charger stands now.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

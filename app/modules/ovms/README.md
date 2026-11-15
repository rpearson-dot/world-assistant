# Car (OVMS) (`ovms`)

Your car's own module (OVMS): the Car page shows every metric it reports — battery, charging, cabin, tyres, position and more — with charge, climate and lock controls over MQTT.

Reads the broker set in Settings → Home Assistant, where the OVMS module publishes (Server V3). Controls (charge, climate, lock) need the broker on TLS and World Assistant's login allowed to write ovms/#

How to use it: [docs/ovms.md](../../../docs/ovms.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | the car's OVMS module (through the MQTT broker) |
| Gives | notifications: car warnings, car gone quiet |
| Endpoints | `/v1/ovms`, `/car` |
| Scheduled jobs | `ovms_watch`, `ovms_housekeep` |
| Assistant tools | `car_status` |
| Plugs into | `assistant.tools`, `startup`, `shutdown` |

## Files

- `api.py`: The car's OVMS data (/v1/ovms): each car's state and warnings, its notifications and a metric's history, and the settings. Reading only: commands (lock,...
- `commands.py`: Sending a car's OVMS module a command over MQTT and reading its reply.
- `listener.py`: Listening to the car's OVMS module on the MQTT broker World Assistant uses (Settings → Home Assistant), from the api. Only one api process listens at a time...
- `page.py`: The Car page (/car): everything the car's OVMS module reports — battery, charging, cabin, tyres, position, motor, module and the Leaf's own dashboard...
- `parse.py`: Reading what an OVMS module publishes over MQTT, and what in it is a warning. Pure, so it is tested on its own.
- `store.py`: The car's OVMS record (migration 0050) and settings.
- `tools.py`: The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

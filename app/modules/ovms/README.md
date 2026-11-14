# Car (OVMS) (`ovms`)

Your car's own module (OVMS): battery, range, 12 V, charging, locks and warnings, read from the MQTT broker.

Reads the broker set in Settings → Home Assistant, where the OVMS module publishes (Server V3). Reading only: commands wait for the broker to have TLS

How to use it: [docs/ovms.md](../../../docs/ovms.md).

| | |
|---|---|
| Endpoints | `/v1/ovms` |
| Scheduled jobs | `ovms_watch`, `ovms_housekeep` |
| Assistant tools | `car_status` |
| Plugs into | `assistant.tools`, `startup`, `shutdown` |

## Files

- `api.py`: The car's OVMS data (/v1/ovms): each car's state and warnings, its notifications and a metric's history, and the settings. Reading only: commands (lock,...
- `listener.py`: Listening to the car's OVMS module on the MQTT broker World Assistant uses (Settings → Home Assistant), from the api. Only one api process listens at a time...
- `parse.py`: Reading what an OVMS module publishes over MQTT, and what in it is a warning. Pure, so it is tested on its own.
- `store.py`: The car's OVMS record (migration 0050) and settings.
- `tools.py`: The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

# Air conditioning (Fujitsu Airstage) (`airstage`)

Fujitsu Airstage air conditioners on your home network: temperatures, on and off, mode, target and fan, from the page, the assistant and Home Assistant.

Talks to each unit directly on your network (its address and device id), never through Fujitsu's cloud

How to use it: [docs/airstage.md](../../../docs/airstage.md).

| | |
|---|---|
| Endpoints | `/v1/airstage`, `/aircon` |
| Scheduled jobs | `airstage_poll`, `airstage_housekeep` |
| Assistant tools | `aircon`, `aircon_set` |
| Plugs into | `assistant.tools`, `startup`, `shutdown` |

## Files

- `api.py`: Air conditioning (/v1/airstage): your Fujitsu Airstage units on the home network, read every two minutes, changed from the page, the assistant or Home...
- `ha.py`: The air conditioners in Home Assistant, over MQTT, as climate entities (climate.wa_aircon_<unit>): mode (off, auto, cool, dry, fan_only, heat), target...
- `page.py`: The air conditioning page (/aircon): each Fujitsu Airstage unit with its temperature, an on/off switch, mode, target and fan, and the units' setup folded...
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).
- `unit.py`: A Fujitsu Airstage indoor unit's own API on the home network (no cloud): POST /GetParam and /SetParam on its address, naming the unit by its device id....

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

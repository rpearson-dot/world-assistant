# Bus Open Data Service (`bods`)

Live bus positions anywhere in Britain; GPS for the bus tracker.

How to use it: [docs/live-buses.md](../../../docs/live-buses.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | Bus Open Data Service: live bus positions |
| Keys | `bods_api_key` |
| Uses when there | [bus](../bus/), [naptan](../naptan/), [tfl](../tfl/) |
| Endpoints | `/v1/live-buses` |
| Assistant tools | `live_buses_near` |
| Plugs into | `assistant.tools`, `map.live_buses`, `key.test` |

## Files

- `bods.py`: Live buses near a point, anywhere in Great Britain, from the Bus Open Data Service.
- `keytest.py`: Does the bods key work? A small live call that changes nothing (provides "key.test").
- `sources/bods/client.py`: Live bus positions from the Bus Open Data Service (BODS), for England, Scotland and Wales.
- `sources/bods/disruptions.py`: Bus, tram and ferry disruptions that local transport authorities publish to BODS, as SIRI-SX.
- `sources/bods/disruptions_probe.py`: Show which authorities publish bus disruptions to BODS, and which disruptions are near you.
- `sources/bods/txc.py`: A bus route's stops and weekday times from a BODS timetable file (TransXChange, "TXC").
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

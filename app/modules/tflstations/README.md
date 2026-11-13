# TfL stations: layout, facilities and lifts (`tflstations`)

Entrances, platforms, lines, toilets and step-free access at every TfL station, and lifts out of service now.

Underground, Overground, DLR, Elizabeth line and tram; National Rail-only stations are not in TfL's data.

How to use it: [docs/stations.md](../../../docs/stations.md).

| | |
|---|---|
| Keys | `tfl_app_key` (optional) |
| Uses when there | [tfl](../tfl/) |
| Endpoints | `/v1/stations` |
| Scheduled jobs | `tfl_stations_sync`, `tfl_station_alerts` |
| Assistant tools | `station` |
| Plugs into | `assistant.tools` |

## Files

- `sources/tflstations/normalise.py`: TfL StopPoint data made into stations, their entrances and platforms, facilities and live closures. Pure.
- `sources/tflstations/sync.py`: TfL stations: layout and facilities once a day, closures (lifts, escalators, entrances) every 10 minutes.
- `stations.py`: TfL stations: find one, see its layout and facilities, and what is closed there now (lifts above all).
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

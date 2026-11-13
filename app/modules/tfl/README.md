# Transport for London (`tfl`)

London bus times and the bus tracker, train boards, London road works.

Works without a key at a low rate.

How to use it: [docs/trains.md](../../../docs/trains.md).

| | |
|---|---|
| Keys | `tfl_app_key` (optional) |
| Uses when there | [nr](../nr/) |
| Endpoints | `/v1/tfl`, `/trains` |
| Assistant tools | `trains` |
| Plugs into | `assistant.tools`, `key.test` |

## Files

- `keytest.py`: Does the tfl key work? A small live call that changes nothing (provides "key.test").
- `sources/tfl/client.py`: Transport for London Unified API client (api.tfl.gov.uk).
- `sources/tfl/normalise.py`: Turn TfL ArrivalDepartures records into departures. Parsing is deliberately tolerant: the field names below are the documented ones, and anything missing...
- `sources/tfl/probe.py`: Look at real TfL responses while setting up.
- `tfl.py`: Transport for London departures API: saved boards, live departures, line status.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

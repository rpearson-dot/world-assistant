# Bus route log (`buslog`)

Every bus on one route (the 213) in the school-run window, recorded from TfL and BODS several times a minute, with every failed or empty request kept too.

Positions need a BODS key; TfL's predictions work without a key, faster with one. Records 07:00 to 09:00 on school days (the school calendar is on the page); a test switch records every day

How to use it: [docs/bus-log.md](../../../docs/bus-log.md).

| | |
|---|---|
| Keys | `bods_api_key`, `tfl_app_key` |
| Endpoints | `/v1/buslog`, `/buslog` |
| Scheduled jobs | `buslog_minute`, `buslog_housekeep` |
| Assistant tools | `bus_log` |
| Plugs into | `assistant.tools` |

## Files

- `api.py`: The bus route recorder's API (/v1/buslog): what it is doing, its settings and school calendar, "record now", and the recorded route, bus positions, requests...
- `collect.py`: The recorder's worker jobs.
- `page.py`: The bus route log page (/buslog): what the recorder is doing, its switches, and a map of the route's stops with each bus as a red box holding its...
- `parse.py`: Reading what TfL and BODS send, without deciding anything: each answer becomes rows that keep every field. Pure: no network, no database, so it is tested...
- `schedule.py`: When the recorder runs: the window (07:00 to 09:00 by default) on school days, or on every day while testing, or now for a while when asked. Pure, apart...
- `store.py`: The recorder's tables (migration 0048) and its settings. Rows are only ever added; nothing here updates or deletes a recorded row (the nightly job drops...
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

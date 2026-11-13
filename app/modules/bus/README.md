# Bus tracker (`bus`)

Has my bus made it, and the next buses at your stops.

How to use it: [docs/bus.md](../../../docs/bus.md).

| | |
|---|---|
| Uses when there | [bods](../bods/), [tfl](../tfl/) |
| Endpoints | `/v1/bus`, `/bus` |
| Scheduled jobs | `poll_bus` |
| Assistant tools | `bus`, `bus_setup` |
| Plugs into | `assistant.tools`, `bus.nearest_slot` |

## Files

- `bus.py`: Bus tracker API: configure trackers, see live buses, the target-stop run and its history.
- `bus_page.py`: A single-page view of the bus tracker, with guided setup. The token is typed in the browser, as on /fuel and /trains. No external resources, and the page...
- `sources/bus/backfill.py`: Record the day's successful runs from the tracker's stored state, for a day the live matching missed.
- `sources/bus/bods_probe.py`: Show what BODS really sends for a bus line, and what this app makes of it.
- `sources/bus/bods_source.py`: A bus line followed with the Bus Open Data Service instead of TfL, for any route in Britain.
- `sources/bus/config.py`: Tracker configuration. Stops of interest are plain data supplied by the owner, so nothing about a particular journey is built into the code or the repository.
- `sources/bus/departures.py`: Next buses at the stops near a point, anywhere BODS reaches: what the bus tracker does for one line, for every line seen nearby.
- `sources/bus/events.py`: Derived facts from tracked trips: leg times, and which buses made the target-stop run.
- `sources/bus/live.py`: Real positions for the buses a tracker is following, from the Bus Open Data Service (BODS).
- `sources/bus/poller.py`: Scheduled bus polling: fetch a line's arrivals, follow each bus, store what is learned.
- `sources/bus/setup.py`: Build a tracker config from a few plain choices, so nobody has to write stop ids or JSON.
- `sources/bus/store.py`: Database access for the bus tracker.
- `sources/bus/trips.py`: Follow each physical bus through the stops of a route.
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

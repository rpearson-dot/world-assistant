# Transport for West Midlands (`tfwm`)

The region's own predicted bus and tram times (West Midlands only).

Not used by the bus tracker yet: run the probe first.

How to use it: [docs/bus.md](../../../docs/bus.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | Transport for West Midlands: predicted bus and tram times |
| Gives | predicted times for the bus tracker |
| Keys | `tfwm_app_id`, `tfwm_app_key` |
| Uses when there | [bods](../bods/), [bus](../bus/) |
| Plugs into | `key.test` |

## Files

- `keytest.py`: Does the tfwm key work? A small live call that changes nothing (provides "key.test").
- `sources/tfwm/__init__.py`: Transport for West Midlands: the region's own predicted bus and tram times (GTFS-Realtime).
- `sources/tfwm/client.py`: Transport for West Midlands (TfWM) open data: the GTFS timetable and the GTFS-Realtime trip updates.
- `sources/tfwm/gtfsrt.py`: Read the parts of a GTFS-Realtime TripUpdates feed that a bus countdown needs.
- `sources/tfwm/probe.py`: Check the TfWM feed from this machine, and see what its identifiers look like.
- `sources/tfwm/routes.py`: Which route number each of TfWM's route ids is.
- `sources/tfwm/routes_probe.py`: Find which of TfWM's route ids is a bus line (TfWM has no route list on its portal).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

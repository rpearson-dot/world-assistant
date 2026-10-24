# TfL stations: layout, facilities and lifts

World Assistant loads every TfL station (Underground, Overground, DLR, Elizabeth line and tram) from TfL's
StopPoint API each night: lines, fare zone, entrances, platforms and which way they go, facilities (lifts, toilets,
car park, wifi) and TfL's step-free note. Every 10 minutes it reads what is closed now: lifts out of service,
escalators, entrances.

National Rail-only stations (Kingston, New Malden, Bishop's Stortford) are not in TfL's data.

## Where you see it

- **Trains tab**: each board shows lifts and step-free routes out at its station, and at the other stations in the
  same building (TfL calls these a hub: Wimbledon's District line, tram and rail stations). A summary sits at the top.
- **API**: `GET /v1/stations?q=waterloo`, `/v1/stations/near?place=home&km=2`, `/v1/stations/940GZZLUWLO`,
  `/v1/stations/alerts?station=...&lifts_only=true`.
- **Assistant**: the `station` tool ("is the lift working at Waterloo?", "what's closed at stations near home?").
- **Graph**: `RailStation`, `Line`, `Entrance` and `StationClosure` nodes; ready-made questions "TfL stations near my
  places" and "Closures at stations near my places".

## Loading it now

It loads overnight. To load at once, or to see what TfL sends:

    docker compose exec worker python -m app.integrations.tflstations.sync
    docker compose exec worker python -m app.integrations.tflstations.sync --probe

Off: `WA_TFL_STATIONS_POLL_ENABLED=false`, or switch the module off on the Modules page.

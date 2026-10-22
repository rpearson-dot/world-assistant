# NaPTAN: where the stops are

NaPTAN is the national register of public transport access points in Great Britain: every bus stop,
rail station, tram and metro stop, ferry terminal and airport entrance, with an id (the ATCO code),
a name and a position. The Department for Transport publishes it under the Open Government Licence;
no key is needed.

World Assistant holds a copy because the bus tracker already stores stop ids (TfL's stop ids are
ATCO codes) without ever knowing where the stops are. With the register beside them, a stop id
becomes a point: it can be drawn, measured from, and related to a saved place.

## How it is loaded

A worker job downloads the register (about 100 MB of CSV) every **Sunday at 02:50**, streams it to a
temporary file, and upserts it into `naptan_stop` in batches. Rows from an earlier load are deleted at
the end, but only if the file held at least 200,000 stops, so a truncated download can add and update
and never wipe. Rows that are inactive, outside the UK, without a usable position, or of a type no
bus or train calls at (taxi ranks, say) are left out.

By hand, from a file you already have or from the source:

```
docker compose exec worker python -m app.integrations.naptan.sync
docker compose exec worker python -m app.integrations.naptan.sync --file naptan.csv
```

`WA_NAPTAN_POLL_ENABLED=false` switches the weekly job off. It is on by default in the compose file.
`WA_NAPTAN_URL` points at a different copy of the register.

## What each record is

| Column | Meaning |
| --- | --- |
| `atco` | the stop's id; what the bus tracker's `sid` is |
| `name`, `street`, `indicator`, `locality` | as published ("Home Road", "High St", "Stop C", "Hackney") |
| `stop_type` | the NaPTAN type, e.g. `BCT` on-street bus stop, `RLY` rail station access area |
| `kind` | the same, coarsened: `bus_stop`, `bus_station`, `rail`, `metro`, `ferry`, `air` |

A station is several records (an access area, entrances, platforms). The map's nationwide view shows
one per station, the access area, so the country is not a smear of platforms.

## Using it

| Where | What |
| --- | --- |
| `GET /v1/stops/near?lat=&lon=&radius_km=&kind=&limit=` | stops around a point, nearest first, with the distance |
| `GET /v1/stops/{atco}` | one stop |
| the `stops` map layer | around the map's centre, up to 3,000 nearest; across the country, stations only |
| the knowledge graph | `BusStop` nodes for the stops a bus tracker watches, `CALLS_AT` from the tracker, `NEAR` to saved places |

Only the tracked stops go into the graph. The register is 400,000 records and the graph is for how
things join up, not a second copy of it.

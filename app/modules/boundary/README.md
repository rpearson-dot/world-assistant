# Council and region boundaries (OS Boundary-Line) (`boundary`)

Which council, county and region a place, stop or roadwork is in.

A large download, loaded on first start.

How to use it: [docs/boundaries.md](../../../docs/boundaries.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | OS Boundary-Line (a download) |
| Gives | which council and region places, stops and road works are in, for other modules |
| Scheduled jobs | `boundary_sync` |

## Files

- `sources/boundaryline/sync.py`: Boundary-Line: where the regions, counties, districts and boroughs of Britain are, from Ordnance Survey.

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

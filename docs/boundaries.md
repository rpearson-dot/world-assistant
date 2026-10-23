# Boundaries and street numbers

Two Ordnance Survey open datasets, held locally so a place, stop, road event or roadwork can be tied to the
council or region it is in, and to the street it is on.

## Boundary-Line

Regions, counties, and districts or boroughs, loaded on first start. `WA_BOUNDARY_KINDS` adds `ward`,
`parish` and `constituency`, which are much bigger. `WA_BOUNDARY_POLL_ENABLED=false` turns it off.

- It is published in May and October. The job runs daily and downloads only when a wanted kind is missing or the
  data is about five months old.
- Each area is simplified as it is loaded (`WA_BOUNDARY_SIMPLIFY_M`, default 20 m). That is plenty to say which
  district a point is in, and it keeps the default kinds to tens of MB in the database.
- Old areas are deleted only when the new file held enough to be the whole thing (the default kinds need at least
  300 districts, 20 counties and 9 regions), so a truncated download removes nothing.
- A point's district: `SELECT name FROM boundary WHERE kind = 'district' AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(lon, lat), 4326))`.

## Open USRN

The number roadworks are reported against (Street Manager attaches one to every job), with each street's line. It
is a million and more streets, so it is **off unless `WA_USRN_POLL_ENABLED=true`**, and reloaded only when missing
or a year old.

## Checking what a download holds

Both read an OS GeoPackage with the standard library (a GeoPackage is an SQLite file). The layer and column names
were written from OS's documentation, not from a real download, so look first:

    docker compose exec worker python -m app.integrations.boundaryline.sync --file bdline_gb.zip --list
    docker compose exec worker python -m app.integrations.usrn.sync --file open_usrn.zip --list

`--list` prints each layer, the kind it would load as, and its columns. If a layer you want shows `kind: None`, say
so and it is a one-line change. With no `--file`, the same commands download and load.

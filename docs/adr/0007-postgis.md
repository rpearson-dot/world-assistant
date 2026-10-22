# 0007 Using the PostGIS that was already installed

**Status:** accepted

**Context.** Migration 0001 created the PostGIS extension and a comment said it was deliberately
left in place, but nothing ever used it. There were no geometry or geography columns and no spatial
functions anywhere. Every distance was worked out in Python: the fuel search computed a bounding box
in degrees, hit a `(lat, lon)` index, and then measured each candidate with haversine. The map
layers were worse — they read every row and filtered afterwards. The graph builder compared every
station against every place.

That is a reasonable workaround for a database without spatial support, which this is not. It also
gets in the way: the knowledge graph's relationships are mostly proximity, and proximity in Python
is a loop over pairs.

Two options: drop the extension and move to the plain `postgres` image, or use it.

**Decision.** Use it. Migration 0018 adds a `geog geography(Point, 4326)` column and a GiST index to
every table that holds coordinates — `place`, `fuel_station`, `nh_sign`, `ha_location`,
`pollen_grid` — and radius searches become `ST_DWithin`, with `ST_Distance` for the measurement.

The column is `GENERATED ALWAYS AS … STORED` from the existing lat and lon rather than written by
the application. It cannot drift, no code has to remember to maintain it, an existing row gets its
point without a backfill, and a row with no coordinates gets NULL rather than a point off the coast
of Africa.

Corridors stay as JSONB waypoints and their line is built per query. There are only ever a handful
of them, so an indexed geometry column would be ceremony for nothing.

The CI test database moves from `postgres:16` to `postgis/postgis:16-3.4`, so tests run against the
same thing production does.

**Consequences.** Radius searches are index-assisted rather than scans, and distances are spheroid
distances rather than great-circle approximations, so they shift by a few hundred metres in the
right direction. The Python distance code in the graph model is gone; the model is now handed
measured pairs and stays pure, which keeps it testable without a database.

PostGIS is now load-bearing: the stack will not run on a plain Postgres image, and a migration
cannot be tested on one. That was already true in spirit — the extension was in migration 0001 —
but it is now true in fact.

Migration 0018 skips tables that are not present, because tests build partial schemas on purpose.
In a real upgrade all five exist.

Moving the test database to the PostGIS image turned up something that had been sitting in
production unnoticed: `postgis/postgis` also installs `postgis_tiger_geocoder`, a United States
census address geocoder, which puts `tiger` on the database's search path and ships its own `place`
table there. Our `place` wins only because `public` comes first on that path. Nothing here geocodes
anything, so migration 0019 drops it, removing 34 empty tables and the shadowing, and pins the
search path to `public`.

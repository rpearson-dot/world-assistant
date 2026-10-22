"""Distance in SQL, so Postgres does the work its spatial index was built for.

Every table holding coordinates has a `geog` column Postgres keeps in step with lat and lon, and a
GiST index over it (migration 0018). These helpers build the fragments that use it, so every caller
asks the same way and none of them goes back to filtering rows in Python.

`ST_DWithin` on a geography is metres on the spheroid, and it is index-assisted, so a radius search
touches the rows near the point rather than the whole table.
"""

from __future__ import annotations

from typing import Any

POINT = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography"

# A corridor's centreline is stored as JSONB waypoints, [[lat, lon], ...]. There are only ever a
# handful of corridors, so the line is built per query rather than stored and kept in step.
CORRIDOR_LINES = """
SELECT c.id, c.name, ST_MakeLine(ARRAY(
         SELECT ST_SetSRID(ST_MakePoint((p->>1)::float8, (p->>0)::float8), 4326)
         FROM jsonb_array_elements(c.centreline) AS p
       ))::geography AS line
FROM nh_corridor c
WHERE c.enabled AND jsonb_array_length(c.centreline) > 1
"""


def within_sql(alias: str = "") -> str:
    """True when the row's point is inside :radius_m of (:lat, :lon). Uses the GiST index."""
    col = f"{alias}.geog" if alias else "geog"
    return f"ST_DWithin({col}, {POINT}, :radius_m)"


def distance_km(alias: str = "") -> str:
    """Distance from (:lat, :lon) in kilometres, for ordering or reporting."""
    col = f"{alias}.geog" if alias else "geog"
    return f"(ST_Distance({col}, {POINT}) / 1000.0)"


def params(lat: float, lon: float, radius_km: float, **extra: Any) -> dict[str, Any]:
    return {"lat": lat, "lon": lon, "radius_m": radius_km * 1000.0, **extra}

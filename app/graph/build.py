"""Rebuild the knowledge graph from Postgres.

Postgres is the source of truth; this reads it and replaces what Neo4j holds. It never writes back,
so the graph can be dropped and rebuilt without losing anything. Run it from the worker on a
schedule, or by hand:

    docker compose exec worker python -m app.graph.build
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.graph import model, store

log = logging.getLogger(__name__)

# Only what the graph needs. The detail stays in Postgres, a join away by id.
QUERIES: dict[str, str] = {
    "place_rows": "SELECT id, name, kind, postcode, lat, lon, origin FROM place",
    "trip_rows": "SELECT id, name, from_place, to_place, enabled, corridor_id FROM trip",
    "corridor_rows": "SELECT id, name, centreline, buffer_m, roads FROM nh_corridor WHERE enabled",
    "event_rows": (
        "SELECT record_id, kind, status, cause, detail, limit_mph, roads FROM nh_event "
        "WHERE cleared_at IS NULL"
    ),
    "station_rows": (
        "SELECT node_id, trading_name, brand_name, postcode, lat, lon, is_motorway, is_supermarket "
        "FROM fuel_station WHERE coords_ok AND NOT permanent_closure"
    ),
    "location_rows": "SELECT id, name, lat, lon FROM ha_location",
    "vehicle_rows": "SELECT id, name, kind FROM vehicle",
    "tracker_rows": "SELECT id, config, enabled FROM bus_tracker",
    "rail_station_rows": "SELECT id, hub, name, modes, lines, zone, step_free, facilities, entrances, platforms, lat, lon FROM tfl_station",
    "rail_part_rows": "SELECT id, station_id, kind, name, lat, lon FROM tfl_station_part WHERE kind = 'entrance'",
    "rail_closure_rows": (
        "SELECT a.id, a.station_id, a.kind, a.description, coalesce(s.name, a.name) AS name FROM tfl_station_alert a "
        "LEFT JOIN tfl_station s ON s.id = a.station_id"
    ),
    "power_cut_rows": "SELECT id, planned, status, customers, restore_at FROM ukpn_fault",
    "supply_rows": "SELECT place_id, kind, code, name FROM ukpn_place_area WHERE kind IN ('gsp', 'primary', 'secondary')",
    # Only the stops a tracker watches, with the position NaPTAN gives them.
    "bus_stop_rows": (
        "SELECT n.atco AS id, n.name, n.indicator, n.lat, n.lon FROM naptan_stop n "
        "WHERE n.atco IN (SELECT sid FROM bus_stop)"
    ),
}
FLOODS = (
    "SELECT w.id, a.label AS name, w.severity_level AS level, w.severity, a.county, a.river_or_sea "
    "FROM flood_warning w JOIN flood_area a ON a.notation = w.area WHERE w.severity_level <= 3"
)
DISRUPTIONS = (
    "SELECT id, category, severity, scope, status, location, has_closures FROM tfl_road_disruption "
    "WHERE geom IS NOT NULL AND (ends_at IS NULL OR ends_at > now())"
)
LOCAL_EVENTS = (
    "SELECT id, source, name, day, type, venue, town, lat, lon FROM local_event WHERE day >= CURRENT_DATE"
)
AREAS = "SELECT id, kind, name, code FROM boundary WHERE kind = ANY(:kinds)"
EVENT_CORRIDORS = "SELECT record_id, corridor_id FROM nh_event_corridor"
CALLS_AT = "SELECT DISTINCT tracker, sid FROM bus_stop"

# Proximity is PostGIS's job: ST_DWithin on the geography columns from migration 0018, index
# assisted, rather than measuring every pair of things in Python.
NEAR_PAIRS = """
SELECT 'Station' AS label, s.node_id AS thing, p.id AS place,
       ST_Distance(s.geog, p.geog) / 1000.0 AS km
FROM fuel_station s JOIN place p ON ST_DWithin(s.geog, p.geog, :near_m)
WHERE s.coords_ok AND NOT s.permanent_closure
UNION ALL
SELECT 'Location', l.id, p.id, ST_Distance(l.geog, p.geog) / 1000.0
FROM ha_location l JOIN place p ON ST_DWithin(l.geog, p.geog, :near_m)
UNION ALL
SELECT 'BusStop', n.atco, p.id, ST_Distance(n.geog, p.geog) / 1000.0
FROM naptan_stop n JOIN place p ON ST_DWithin(n.geog, p.geog, :near_m)
WHERE n.atco IN (SELECT sid FROM bus_stop)
UNION ALL
SELECT 'RailStation', s.id, p.id, ST_Distance(s.geog, p.geog) / 1000.0
FROM tfl_station s JOIN place p ON ST_DWithin(s.geog, p.geog, :near_m)
UNION ALL
SELECT 'PowerCut', f.id, p.id, ST_Distance(f.geog, p.geog) / 1000.0
FROM ukpn_fault f JOIN place p ON ST_DWithin(f.geog, p.geog, :near_m)
UNION ALL
SELECT 'StreetWork', w.id, p.id, ST_Distance(w.geog, p.geog) / 1000.0
FROM ukpn_works w JOIN place p ON ST_DWithin(w.geog, p.geog, :near_m)
WHERE w.stage = 'open' OR w.ends_at IS NULL OR w.ends_at > now()
UNION ALL
SELECT 'LocalEvent', e.id, p.id, ST_Distance(e.geog, p.geog) / 1000.0
FROM local_event e JOIN place p ON ST_DWithin(e.geog, p.geog, :near_m)
WHERE e.day >= CURRENT_DATE
UNION ALL
SELECT 'Disruption', d.id, p.id, ST_Distance(d.geom, p.geog) / 1000.0
FROM tfl_road_disruption d JOIN place p ON ST_DWithin(d.geom, p.geog, :near_m)
WHERE d.geom IS NOT NULL AND (d.ends_at IS NULL OR d.ends_at > now())
"""

# Which disruptions lie along a watched corridor (same buffer as fuel stations).
DISRUPTION_CORRIDORS = """
WITH lines AS (
  SELECT c.id, ST_MakeLine(ARRAY(
           SELECT ST_SetSRID(ST_MakePoint((pt->>1)::float8, (pt->>0)::float8), 4326)
           FROM jsonb_array_elements(c.centreline) AS pt
         ))::geography AS line
  FROM nh_corridor c
  WHERE c.enabled AND jsonb_array_length(c.centreline) > 1
)
SELECT d.id, l.id FROM tfl_road_disruption d JOIN lines l ON ST_DWithin(d.geom, l.line, :along_m)
WHERE d.geom IS NOT NULL AND (d.ends_at IS NULL OR d.ends_at > now())
"""

# What lies inside which administrative area (Boundary-Line, kinds chosen in settings). Stations are left
# out: thousands of them would bury the areas.
INSIDE_PAIRS = """
SELECT 'Place' AS label, p.id AS thing, b.id AS area
FROM place p JOIN boundary b ON b.kind = ANY(:kinds) AND ST_Intersects(b.geom, p.geog::geometry)
UNION ALL
SELECT 'BusStop', n.atco, b.id
FROM naptan_stop n JOIN boundary b ON b.kind = ANY(:kinds) AND ST_Intersects(b.geom, n.geog::geometry)
WHERE n.atco IN (SELECT sid FROM bus_stop)
UNION ALL
SELECT 'RailStation', s.id, b.id
FROM tfl_station s JOIN boundary b ON b.kind = ANY(:kinds) AND ST_Intersects(b.geom, s.geog::geometry)
UNION ALL
SELECT 'LocalEvent', e.id, b.id
FROM local_event e JOIN boundary b ON b.kind = ANY(:kinds) AND ST_Intersects(b.geom, e.geog::geometry)
WHERE e.day >= CURRENT_DATE
UNION ALL
SELECT 'Disruption', d.id, b.id
FROM tfl_road_disruption d JOIN boundary b ON b.kind = ANY(:kinds) AND ST_Intersects(b.geom, d.geom::geometry)
WHERE d.geom IS NOT NULL AND (d.ends_at IS NULL OR d.ends_at > now())
"""

# The next wider area each area sits in (a ward in a district, a district in a county, a county in a region).
AREA_PARENTS = """
WITH ranked AS (
  SELECT id, geom, CASE kind WHEN 'region' THEN 3 WHEN 'county' THEN 2 WHEN 'district' THEN 1 ELSE 0 END AS rank
  FROM boundary WHERE kind = ANY(:kinds)
)
SELECT DISTINCT ON (c.id) c.id, p.id
FROM ranked c JOIN ranked p ON p.rank > c.rank AND ST_Intersects(p.geom, ST_PointOnSurface(c.geom))
ORDER BY c.id, p.rank
"""

# Which saved places a live warning's area contains, and which corridors it crosses.
FLOOD_COVERS = """
SELECT w.id, p.id FROM flood_warning w
JOIN flood_area a ON a.notation = w.area AND a.geom IS NOT NULL
JOIN place p ON ST_DWithin(a.geom, p.geog, 0)
WHERE w.severity_level <= 3
"""

FLOOD_CROSSES = """
WITH lines AS (
  SELECT c.id, ST_MakeLine(ARRAY(
           SELECT ST_SetSRID(ST_MakePoint((pt->>1)::float8, (pt->>0)::float8), 4326)
           FROM jsonb_array_elements(c.centreline) AS pt
         ))::geography AS line
  FROM nh_corridor c
  WHERE c.enabled AND jsonb_array_length(c.centreline) > 1
)
SELECT w.id, l.id FROM flood_warning w
JOIN flood_area a ON a.notation = w.area AND a.geom IS NOT NULL
JOIN lines l ON ST_DWithin(a.geom, l.line, 0)
WHERE w.severity_level <= 3
"""

# UK Power Networks' street works near a place or along a watched corridor (thousands exist; only these go in).
WORK_CORRIDORS = """
WITH lines AS (
  SELECT c.id, ST_MakeLine(ARRAY(
           SELECT ST_SetSRID(ST_MakePoint((pt->>1)::float8, (pt->>0)::float8), 4326)
           FROM jsonb_array_elements(c.centreline) AS pt
         ))::geography AS line
  FROM nh_corridor c
  WHERE c.enabled AND jsonb_array_length(c.centreline) > 1
)
SELECT w.id, l.id FROM ukpn_works w JOIN lines l ON ST_DWithin(w.geog, l.line, :along_m)
WHERE w.stage = 'open' OR w.ends_at IS NULL OR w.ends_at > now()
"""
STREET_WORKS = """
SELECT w.id, w.stage, w.street, w.traffic, w.starts_at, w.ends_at FROM ukpn_works w
WHERE (w.stage = 'open' OR w.ends_at IS NULL OR w.ends_at > now()) AND (w.id = ANY(:ids)
  OR EXISTS (SELECT 1 FROM place p WHERE ST_DWithin(w.geog, p.geog, :near_m)))
"""

ALONG_PAIRS = """
WITH lines AS (
  SELECT c.id, ST_MakeLine(ARRAY(
           SELECT ST_SetSRID(ST_MakePoint((pt->>1)::float8, (pt->>0)::float8), 4326)
           FROM jsonb_array_elements(c.centreline) AS pt
         ))::geography AS line
  FROM nh_corridor c
  WHERE c.enabled AND jsonb_array_length(c.centreline) > 1
)
SELECT s.node_id AS station, l.id AS corridor, ST_Distance(s.geog, l.line) / 1000.0 AS km
FROM fuel_station s JOIN lines l ON ST_DWithin(s.geog, l.line, :along_m)
WHERE s.coords_ok AND NOT s.permanent_closure
"""


async def read(
    engine: AsyncEngine, near_km: float, along_km: float, area_kinds: list[str] | None = None
) -> dict[str, Any]:
    """One read of everything the graph is made from, proximity included."""
    out: dict[str, Any] = {}
    area_kinds = area_kinds or ["region", "county", "district"]
    async with engine.connect() as conn:
        for name, sql in QUERIES.items():
            out[name] = [dict(r._mapping) for r in await conn.execute(text(sql))]
        out["event_corridors"] = [tuple(r) for r in await conn.execute(text(EVENT_CORRIDORS))]
        out["flood_rows"] = [dict(r._mapping) for r in await conn.execute(text(FLOODS))]
        out["flood_covers"] = [tuple(r) for r in await conn.execute(text(FLOOD_COVERS))]
        out["flood_crosses"] = [tuple(r) for r in await conn.execute(text(FLOOD_CROSSES))]
        out["disruption_rows"] = [dict(r._mapping) for r in await conn.execute(text(DISRUPTIONS))]
        out["local_event_rows"] = [dict(r._mapping) for r in await conn.execute(text(LOCAL_EVENTS))]
        kinds = {"kinds": area_kinds}
        out["area_rows"] = [dict(r._mapping) for r in await conn.execute(text(AREAS), kinds)]
        out["inside_pairs"] = [tuple(r) for r in await conn.execute(text(INSIDE_PAIRS), kinds)]
        out["area_parents"] = [tuple(r) for r in await conn.execute(text(AREA_PARENTS), kinds)]
        out["disruption_corridors"] = [tuple(r) for r in await conn.execute(
            text(DISRUPTION_CORRIDORS), {"along_m": along_km * 1000.0})]  # fmt: skip
        out["calls_at"] = [tuple(r) for r in await conn.execute(text(CALLS_AT))]
        out["work_corridors"] = [tuple(r) for r in await conn.execute(
            text(WORK_CORRIDORS), {"along_m": along_km * 1000.0})]  # fmt: skip
        out["street_work_rows"] = [dict(r._mapping) for r in await conn.execute(
            text(STREET_WORKS), {"ids": [w for w, _ in out["work_corridors"]], "near_m": near_km * 1000.0})]  # fmt: skip
        out["near_pairs"] = [tuple(r) for r in await conn.execute(
            text(NEAR_PAIRS), {"near_m": near_km * 1000.0})]  # fmt: skip
        out["along_pairs"] = [tuple(r) for r in await conn.execute(
            text(ALONG_PAIRS), {"along_m": along_km * 1000.0})]  # fmt: skip
    return out


async def rebuild(engine: AsyncEngine) -> dict[str, Any]:
    """Read Postgres, derive the graph, replace what Neo4j holds. Returns what it wrote."""
    s = get_settings()
    started = time.monotonic()
    kinds = [k.strip() for k in s.boundary_kinds.split(",") if k.strip()]
    rows = await read(engine, s.graph_near_km, s.graph_along_km, kinds)
    graph = model.build(**rows)
    built = uuid.uuid4().hex
    counts = await store.write(graph, built)
    took = round(time.monotonic() - started, 2)
    log.info("graph rebuilt: %s in %ss", counts, took)
    return {"ok": True, "built": built, "seconds": took, **counts}


async def graph_build(ctx: dict[str, Any]) -> dict[str, Any]:
    """arq job."""
    if not get_settings().graph_enabled:
        return {"ok": False, "message": "the knowledge graph is switched off"}
    try:
        return await rebuild(ctx["engine"])
    except store.GraphUnavailable as exc:
        log.warning("graph not rebuilt: %s", exc)
        return {"ok": False, "message": str(exc)}


async def _cli() -> int:
    logging.basicConfig(level=logging.INFO)
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        print(await rebuild(engine))
        return 0
    except store.GraphUnavailable as exc:
        print(f"not built: {exc}")
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))

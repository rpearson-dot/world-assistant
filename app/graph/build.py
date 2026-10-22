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
}
EVENT_CORRIDORS = "SELECT record_id, corridor_id FROM nh_event_corridor"

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


async def read(engine: AsyncEngine, near_km: float, along_km: float) -> dict[str, Any]:
    """One read of everything the graph is made from, proximity included."""
    out: dict[str, Any] = {}
    async with engine.connect() as conn:
        for name, sql in QUERIES.items():
            out[name] = [dict(r._mapping) for r in await conn.execute(text(sql))]
        out["event_corridors"] = [tuple(r) for r in await conn.execute(text(EVENT_CORRIDORS))]
        out["near_pairs"] = [tuple(r) for r in await conn.execute(
            text(NEAR_PAIRS), {"near_m": near_km * 1000.0})]  # fmt: skip
        out["along_pairs"] = [tuple(r) for r in await conn.execute(
            text(ALONG_PAIRS), {"along_m": along_km * 1000.0})]  # fmt: skip
    return out


async def rebuild(engine: AsyncEngine) -> dict[str, Any]:
    """Read Postgres, derive the graph, replace what Neo4j holds. Returns what it wrote."""
    s = get_settings()
    started = time.monotonic()
    rows = await read(engine, s.graph_near_km, s.graph_along_km)
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

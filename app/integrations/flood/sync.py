"""Environment Agency flood warnings: the current list, and the boundary of any area not yet held.

The list is updated every 15 minutes by the EA and is complete (every current warning, and those
that ended in the last day), so anything stored that is not in it has gone. That deletion only
happens when the response was well-formed, so a failed fetch can never clear real warnings.

    python -m app.integrations.flood.sync
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.flood.normalise import geometry_of, parse_item
from app.jobs import tracked

log = logging.getLogger("wa.flood")

BASE = "https://environment.data.gov.uk/flood-monitoring"
POLYGONS_PER_RUN = 25  # a first run after a big storm may need hundreds; the rest follow in later runs
USER_AGENT = "world-assistant (self-hosted)"

UPSERT_AREA = text(
    """
    INSERT INTO flood_area (notation, label, county, river_or_sea, ea_area, polygon_url)
    VALUES (:notation, :label, :county, :river_or_sea, :ea_area, :polygon_url)
    ON CONFLICT (notation) DO UPDATE SET label = EXCLUDED.label, county = EXCLUDED.county,
      river_or_sea = EXCLUDED.river_or_sea, ea_area = EXCLUDED.ea_area,
      polygon_url = COALESCE(EXCLUDED.polygon_url, flood_area.polygon_url)
    """
)
UPSERT_WARNING = text(
    """
    INSERT INTO flood_warning (id, area, severity_level, severity, message, is_tidal, raised_at,
                               severity_changed_at, message_changed_at, seen)
    VALUES (:id, :area, :severity_level, :severity, :message, :is_tidal, :raised_at,
            :severity_changed_at, :message_changed_at, :seen)
    ON CONFLICT (id) DO UPDATE SET area = EXCLUDED.area, severity_level = EXCLUDED.severity_level,
      severity = EXCLUDED.severity, message = EXCLUDED.message, is_tidal = EXCLUDED.is_tidal,
      raised_at = EXCLUDED.raised_at, severity_changed_at = EXCLUDED.severity_changed_at,
      message_changed_at = EXCLUDED.message_changed_at, seen = EXCLUDED.seen
    """
)
SET_GEOM = text(
    """
    UPDATE flood_area SET geom_at = now(),
      geom = ST_SetSRID(ST_MakeValid(ST_GeomFromGeoJSON(:g)), 4326)::geography
    WHERE notation = :n
    """
)
NEEDS_POLYGON = text(
    """
    SELECT a.notation, a.polygon_url FROM flood_area a
    WHERE a.geom IS NULL AND a.polygon_url IS NOT NULL
      AND a.notation IN (SELECT area FROM flood_warning WHERE severity_level <= 3)
    ORDER BY a.notation LIMIT :n
    """
)


async def fetch_items(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get(f"{BASE}/id/floods")
    response.raise_for_status()
    body = response.json()
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise ValueError("the flood list had no items field; treating it as a failed fetch")  # noqa: TRY004
    return items


async def store_warnings(engine: AsyncEngine, items: list[dict[str, Any]], tz: str) -> dict[str, int]:
    parsed = [p for i in items if (p := parse_item(i, tz)) is not None]
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        if parsed:
            areas = {p["area"]["notation"]: p["area"] for p in parsed}
            await conn.execute(UPSERT_AREA, list(areas.values()))
            await conn.execute(UPSERT_WARNING, [{**p["warning"], "seen": now} for p in parsed])
        gone = (await conn.execute(text("DELETE FROM flood_warning WHERE seen < :n"), {"n": now})).rowcount
    live = sum(1 for p in parsed if p["warning"]["severity_level"] <= 3)
    return {"warnings": len(parsed), "live": live, "ended": gone, "skipped": len(items) - len(parsed)}


async def fetch_polygons(engine: AsyncEngine, client: httpx.AsyncClient) -> dict[str, int]:
    async with engine.connect() as conn:
        todo = (await conn.execute(NEEDS_POLYGON, {"n": POLYGONS_PER_RUN})).all()
    got = failed = 0
    for notation, url in todo:
        try:
            response = await client.get(url)
            response.raise_for_status()
            geometry = geometry_of(response.json())
        except (httpx.HTTPError, ValueError):
            geometry = None
        if geometry is None:
            failed += 1
            continue
        async with engine.begin() as conn:
            await conn.execute(SET_GEOM, {"g": json.dumps(geometry), "n": notation})
        got += 1
    return {"boundaries": got, "boundaries_failed": failed}


async def sync(engine: AsyncEngine, client: httpx.AsyncClient | None = None) -> dict[str, int]:
    tz = get_settings().tfl_naive_tz
    owned = client is None
    client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        items = await fetch_items(client)
        result = await store_warnings(engine, items, tz)
        result.update(await fetch_polygons(engine, client))
        return result
    finally:
        if owned:
            await client.aclose()


async def flood_poll(ctx: dict[str, Any]) -> Any:
    """arq job."""

    async def run() -> Any:
        if not get_settings().flood_poll_enabled:
            return "disabled"
        return await sync(ctx["engine"])

    return await tracked(ctx["engine"], "flood", run)


async def _cli() -> int:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        print(await sync(engine))
        return 0
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))

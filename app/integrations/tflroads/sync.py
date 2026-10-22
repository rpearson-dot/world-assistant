"""TfL road disruptions: the full London list, every 15 minutes.

The list is complete (every current and scheduled disruption), so a stored one that is not in it has ended. That
deletion only happens when the response was a well-formed list, and never on an empty list over a populated table.

    python -m app.integrations.tflroads.sync
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.tfl.client import TflClient, TflError
from app.integrations.tflroads.normalise import parse_item
from app.jobs import tracked
from app.ratelimit import SlidingWindowLimiter

log = logging.getLogger("wa.tflroads")

UPSERT = text(
    """
    INSERT INTO tfl_road_disruption (id, category, sub_category, severity, status, scope, location, comment,
      update_text, starts_at, ends_at, has_closures, corridors, last_modified, seen, geom)
    VALUES (:id, :category, :sub_category, :severity, :status, :scope, :location, :comment, :update_text,
      :starts_at, :ends_at, :has_closures, :corridors, :last_modified, :seen,
      CASE WHEN CAST(:geom AS text) IS NULL THEN NULL
           ELSE ST_SetSRID(ST_MakeValid(ST_GeomFromGeoJSON(CAST(:geom AS text))), 4326)::geography END)
    ON CONFLICT (id) DO UPDATE SET category = EXCLUDED.category, sub_category = EXCLUDED.sub_category,
      severity = EXCLUDED.severity, status = EXCLUDED.status, scope = EXCLUDED.scope, location = EXCLUDED.location,
      comment = EXCLUDED.comment, update_text = EXCLUDED.update_text, starts_at = EXCLUDED.starts_at,
      ends_at = EXCLUDED.ends_at, has_closures = EXCLUDED.has_closures, corridors = EXCLUDED.corridors,
      last_modified = EXCLUDED.last_modified, seen = EXCLUDED.seen, geom = EXCLUDED.geom
    """
)
SUSPICIOUS_EMPTY_OVER = 20  # an empty list over more rows than this is treated as a failed fetch


async def store(engine: AsyncEngine, items: list[Any], tz: str) -> dict[str, int]:
    parsed = [p for i in items if (p := parse_item(i, tz)) is not None]
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        held = (await conn.execute(text("SELECT count(*) FROM tfl_road_disruption"))).scalar_one()
        if not parsed and held > SUSPICIOUS_EMPTY_OVER:
            raise TflError("TfL returned no disruptions; keeping the ones stored")
        if parsed:
            await conn.execute(
                UPSERT,
                [{**p, "seen": now, "geom": json.dumps(p["geom"]) if p["geom"] else None} for p in parsed],
            )
        gone = (await conn.execute(text("DELETE FROM tfl_road_disruption WHERE seen < :n"), {"n": now})).rowcount
    return {"disruptions": len(parsed), "ended": gone, "skipped": len(items) - len(parsed),
            "positioned": sum(1 for p in parsed if p["geom"])}  # fmt: skip


async def fetch(client: TflClient) -> list[Any]:
    data = await client.get("/Road/all/Disruption")
    if not isinstance(data, list):
        raise TflError("the road disruption list was not a list; treating it as a failed fetch")
    return data


async def sync(engine: AsyncEngine, client: TflClient) -> dict[str, int]:
    return await store(engine, await fetch(client), get_settings().tfl_naive_tz)


async def tflroads_poll(ctx: dict[str, Any]) -> Any:
    """arq job."""

    async def run() -> Any:
        s = get_settings()
        if not s.tflroads_poll_enabled:
            return "disabled"
        key = s.tfl_app_key
        limiter = SlidingWindowLimiter(ctx["redis"], "tfl", 250 if key else 30)
        async with TflClient(key, limiter=limiter, timeout=60.0) as client:
            return await sync(ctx["engine"], client)

    return await tracked(ctx["engine"], "tflroads", run)


async def _cli() -> int:
    s = get_settings()
    engine = create_async_engine(s.database_url, pool_pre_ping=True)
    redis = Redis.from_url(s.redis_dsn, decode_responses=True)
    try:
        key = s.tfl_app_key
        async with TflClient(key, limiter=SlidingWindowLimiter(redis, "tfl", 250 if key else 30), timeout=60.0) as c:
            print(await sync(engine, c))
        return 0
    except TflError as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await engine.dispose()
        await redis.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))

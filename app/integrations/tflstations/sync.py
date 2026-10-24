"""TfL stations: layout and facilities once a day, closures (lifts, escalators, entrances) every 10 minutes.

    python -m app.integrations.tflstations.sync            load stations and closures now
    python -m app.integrations.tflstations.sync --probe    show what TfL sends and what this makes of it

A station list that comes back much shorter than the one stored is treated as a failed read and not applied. The
closures are replaced on each read, so one that has been fixed disappears.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.tfl.client import TflClient, TflError
from app.integrations.tflstations.normalise import MODES, alerts, stations
from app.jobs import tracked
from app.ratelimit import SlidingWindowLimiter

UPSERT_STATION = text(
    """
    INSERT INTO tfl_station (id, hub, name, modes, lines, zone, step_free, facilities, entrances, platforms, lat, lon, seen)
    VALUES (:id, :hub, :name, :modes, :lines, :zone, :step_free, CAST(:facilities AS jsonb), :entrances, :platforms,
            :lat, :lon, :seen)
    ON CONFLICT (id) DO UPDATE SET hub = EXCLUDED.hub, name = EXCLUDED.name, modes = EXCLUDED.modes,
      lines = EXCLUDED.lines, zone = EXCLUDED.zone, step_free = EXCLUDED.step_free, facilities = EXCLUDED.facilities,
      entrances = EXCLUDED.entrances, platforms = EXCLUDED.platforms, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
      seen = EXCLUDED.seen
    """
)
INSERT_PART = text(
    """
    INSERT INTO tfl_station_part (id, station_id, kind, name, lines, towards, lat, lon)
    VALUES (:id, :station_id, :kind, :name, :lines, :towards, :lat, :lon)
    ON CONFLICT (id) DO NOTHING
    """
)
INSERT_ALERT = text(
    """
    INSERT INTO tfl_station_alert (id, station_id, name, kind, description, starts_at, ends_at, seen)
    VALUES (:id, :station_id, :name, :kind, :description, :starts_at, :ends_at, :seen)
    ON CONFLICT (id) DO NOTHING
    """
)
SHRINK_LIMIT = 0.5  # fewer than half the stations stored: a failed read, not 200 stations closing


def _client(redis: Redis) -> TflClient:
    key = get_settings().tfl_app_key
    return TflClient(key, limiter=SlidingWindowLimiter(redis, "tfl", 250 if key else 30), timeout=180.0)


async def store_stations(engine: AsyncEngine, rows: list[dict[str, Any]], parts: list[dict[str, Any]]) -> dict[str, int]:
    async with engine.begin() as conn:
        held = (await conn.execute(text("SELECT count(*) FROM tfl_station"))).scalar_one()
        if held and len(rows) < held * SHRINK_LIMIT:
            raise TflError(f"TfL returned {len(rows)} stations against {held} stored; keeping the stored ones")
        if rows:
            await conn.execute(UPSERT_STATION, [{**r, "facilities": json.dumps(r["facilities"])} for r in rows])
            await conn.execute(text("DELETE FROM tfl_station WHERE seen < :n"), {"n": rows[0]["seen"]})
            await conn.execute(text("DELETE FROM tfl_station_part"))
            if parts:
                await conn.execute(INSERT_PART, parts)
    return {"stations": len(rows), "entrances": sum(p["kind"] == "entrance" for p in parts),
            "platforms": sum(p["kind"] == "platform" for p in parts)}  # fmt: skip


async def store_alerts(engine: AsyncEngine, rows: list[dict[str, Any]]) -> dict[str, int]:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM tfl_station_alert"))
        if rows:
            await conn.execute(INSERT_ALERT, rows)
    return {"closures": len(rows), "lifts": sum(r["kind"] == "lift" for r in rows)}


async def fetch_stations(c: TflClient) -> Any:
    return await c.get(f"/StopPoint/Mode/{','.join(MODES)}")


async def fetch_alerts(c: TflClient) -> Any:
    return await c.get(f"/StopPoint/Mode/{','.join(MODES)}/Disruption", {"includeRouteBlockedStops": "false"})


async def tfl_stations_sync(ctx: dict[str, Any]) -> Any:
    """arq job: the stations, their layout and facilities (daily)."""

    async def run() -> Any:
        if not get_settings().tfl_stations_poll_enabled:
            return "disabled"
        async with _client(ctx["redis"]) as c:
            rows, parts = stations(await fetch_stations(c), datetime.now(UTC))
        return await store_stations(ctx["engine"], rows, parts)

    return await tracked(ctx["engine"], "tfl_stations", run)


async def tfl_station_alerts(ctx: dict[str, Any]) -> Any:
    """arq job: what is closed now (every 10 minutes)."""

    async def run() -> Any:
        if not get_settings().tfl_stations_poll_enabled:
            return "disabled"
        async with _client(ctx["redis"]) as c:
            rows = alerts(await fetch_alerts(c), datetime.now(UTC))
        return await store_alerts(ctx["engine"], rows)

    return await tracked(ctx["engine"], "tfl_station_alerts", run)


async def _probe(c: TflClient) -> None:
    data = await fetch_stations(c)
    items = data.get("stopPoints", []) if isinstance(data, dict) else data
    print(f"/StopPoint/Mode: {type(data).__name__}, {len(items)} stop points; types:",
          dict(Counter(str(i.get('stopType')) for i in items if isinstance(i, dict)).most_common(8)))  # fmt: skip
    rows, parts = stations(data, datetime.now(UTC))
    print(f"made {len(rows)} stations, {sum(p['kind'] == 'entrance' for p in parts)} entrances, "
          f"{sum(p['kind'] == 'platform' for p in parts)} platforms")  # fmt: skip
    for r in sorted(rows, key=lambda r: -r["entrances"])[:3]:
        print("  ", {k: r[k] for k in ("id", "hub", "name", "modes", "zone", "step_free", "entrances", "platforms")},
              "facilities:", {k: list(v)[:6] for k, v in r["facilities"].items()})  # fmt: skip
    kids = Counter(str(k.get("stopType")) for i in items if isinstance(i, dict) for k in i.get("children") or [])
    print("child stop types:", dict(kids.most_common(10)))
    found = await fetch_alerts(c)
    got = alerts(found, datetime.now(UTC))
    print(f"/Disruption: {len(found) if isinstance(found, list) else type(found).__name__} items, {len(got)} closures,",
          dict(Counter(a["kind"] for a in got)))  # fmt: skip
    for a in got[:3]:
        print("  ", {k: a[k] for k in ("station_id", "name", "kind")}, a["description"][:120])
    if isinstance(found, list) and found:
        print("first raw keys:", sorted(found[0].keys()) if isinstance(found[0], dict) else found[0])


async def _cli(argv: list[str]) -> int:
    s = get_settings()
    redis = Redis.from_url(s.redis_dsn, decode_responses=True)
    engine = create_async_engine(s.database_url, pool_pre_ping=True)
    try:
        async with _client(redis) as c:
            if "--probe" in argv:
                await _probe(c)
                return 0
            rows, parts = stations(await fetch_stations(c), datetime.now(UTC))
            print(await store_stations(engine, rows, parts))
            print(await store_alerts(engine, alerts(await fetch_alerts(c), datetime.now(UTC))))
        return 0
    except TflError as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await engine.dispose()
        await redis.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli(sys.argv[1:])))

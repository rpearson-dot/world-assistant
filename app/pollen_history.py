"""A running history of pollen readings: one value per place, day and allergen, kept 90 days.

Each fetch records today's levels (a later fetch the same day replaces the earlier one), so the
history is what the service said about each day on that day. The worker fetches for every saved
place once a day (the provider allows 40 requests a day in all), so it fills in whether or not
anyone opens the page.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.integrations.pollen.client import DEFAULT_BASE, PollenClient, PollenError
from app.pollen_budget import PollenBudgetError

log = logging.getLogger("wa.pollen")
KEEP_DAYS = 90
RISK = "_risk"
CACHE_SECONDS = 24 * 3600  # the provider allows 40 requests a day, and a fetch covers four days


def cache_key(lat: float, lon: float, country: str, today: date) -> str:
    import hashlib

    raw = json.dumps([round(lat, 2), round(lon, 2), country, today.isoformat()]).encode()
    return "wa:pollen:" + hashlib.sha256(raw).hexdigest()[:24]


def readings(forecast: dict[str, Any]) -> list[tuple[str, str | None, float]]:
    """Today's values (allergen id, name, value) from a parsed forecast. Missing values are skipped."""
    out: list[tuple[str, str | None, float]] = []
    for a in forecast.get("allergens") or []:
        level = (a.get("levels") or [None])[0]
        key = a.get("id")
        if level is not None and key is not None:
            out.append((str(key), a.get("name"), float(level)))
    risk = (forecast.get("allergy_risk") or [None])[0]
    if risk is not None:
        out.append((RISK, "Allergy risk", float(risk)))
    return out


async def record(engine: AsyncEngine, place_id: str, today: date, forecast: dict[str, Any]) -> int:
    rows = readings(forecast)
    if not rows:
        return 0
    async with engine.begin() as conn:
        for allergen, name, value in rows:
            await conn.execute(
                text(
                    "INSERT INTO pollen_reading (place_id, day, allergen, name, value) "
                    "VALUES (:p, :d, :a, :n, :v) "
                    "ON CONFLICT (place_id, day, allergen) DO UPDATE "
                    "SET name = EXCLUDED.name, value = EXCLUDED.value, recorded_at = now()"
                ),
                {"p": place_id, "d": today, "a": allergen, "n": name, "v": value},
            )
    return len(rows)


async def prune(engine: AsyncEngine, today: date) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM pollen_reading WHERE day < :cut"),
            {"cut": today - timedelta(days=KEEP_DAYS)},
        )


def shape(rows: list[tuple[str, date, str, str | None, float]], first: date, last: date) -> dict[str, Any]:
    """Rows (place, day, allergen, name, value) into aligned series over first..last."""
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    index = {d: i for i, d in enumerate(days)}
    series: dict[str, dict[str, Any]] = {}
    risk: list[float | None] = [None] * len(days)
    for _place, day, allergen, name, value in rows:
        i = index.get(day)
        if i is None:
            continue
        if allergen == RISK:
            risk[i] = value
            continue
        s = series.setdefault(allergen, {"id": allergen, "name": name, "values": [None] * len(days)})
        s["values"][i] = value
        s["name"] = name or s["name"]
    total: list[float | None] = []
    for i in range(len(days)):
        vals = [s["values"][i] for s in series.values() if s["values"][i] is not None]
        total.append(sum(vals) if vals else None)
    ordered = sorted(series.values(), key=lambda s: -(max((v for v in s["values"] if v is not None), default=0)))
    recorded = sum(1 for t in total if t is not None)
    return {
        "days": [d.isoformat() for d in days],
        "total": total,
        "allergy_risk": risk,
        "allergens": ordered,
        "days_recorded": recorded,
    }


async def history(engine: AsyncEngine, place_id: str, today: date, days: int = KEEP_DAYS) -> dict[str, Any]:
    days = max(1, min(days, KEEP_DAYS))
    first = today - timedelta(days=days - 1)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT place_id, day, allergen, name, value FROM pollen_reading "
                    "WHERE place_id = :p AND day BETWEEN :a AND :b ORDER BY day"
                ),
                {"p": place_id, "a": first, "b": today},
            )
        ).all()
    return shape([tuple(r) for r in rows], first, today)  # type: ignore[arg-type]


async def pollen_sync(ctx: dict[str, Any]) -> Any:
    """Worker job: fetch the forecast for every saved place, store today's values, warm the cache."""
    from app.jobs import tracked
    from app.pollen_budget import PollenLimiter

    settings = get_settings()

    async def run() -> Any:
        if not settings.pollen_api_key:
            return "no key"
        engine: AsyncEngine = ctx["engine"]
        today = datetime.now(ZoneInfo(settings.local_tz)).date()
        async with engine.connect() as conn:
            places = (await conn.execute(text("SELECT id, lat, lon FROM place WHERE origin IS DISTINCT FROM 'home-assistant' ORDER BY id"))).all()
        fetched: dict[tuple[float, float], dict[str, Any]] = {}
        stored = 0
        limiter = PollenLimiter(ctx["redis"], settings.pollen_daily_limit)
        async with PollenClient(settings.pollen_api_key, settings.pollen_base_url or DEFAULT_BASE, limiter=limiter) as c:
            for pid, lat, lon in places:
                spot = (round(lat, 2), round(lon, 2))
                try:
                    if spot not in fetched:
                        fetched[spot] = await c.forecast(lat, lon, today, country=settings.pollen_country)
                        out = {"source": "Austrian Pollen Information Service, www.polleninformation.at", **fetched[spot]}
                        try:
                            await ctx["redis"].set(
                                cache_key(lat, lon, settings.pollen_country, today), json.dumps(out), ex=CACHE_SECONDS
                            )
                        except Exception:  # noqa: BLE001 - the cache is optional
                            log.warning("could not warm the pollen cache")
                    stored += await record(engine, pid, today, fetched[spot])
                except PollenError as exc:
                    log.warning("pollen fetch for %s failed: %s", pid, exc)
                    if isinstance(exc, PollenBudgetError):
                        break  # nothing more can be fetched today
        await prune(engine, today)
        return {"places": len(places), "values": stored}

    return await tracked(ctx["engine"], "pollen_sync", run)


async def _cli() -> int:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import create_async_engine

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
    try:
        print(await pollen_sync({"engine": engine, "redis": redis}))
        return 0
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":  # python -m app.pollen_history: fetch and record once, now
    import asyncio
    import sys

    sys.exit(asyncio.run(_cli()))

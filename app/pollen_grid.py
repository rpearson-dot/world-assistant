"""A nationwide pollen forecast, fetched on a lattice and kept here.

The provider answers one point at a time, so the worker asks for a lattice of points over Britain
and Northern Ireland, a few points at a time (the provider allows 40 requests a day in all, and a
forecast covers four days), and stores every answer. Anyone (the web page, the
assistant, Home Assistant) then gets the answer for any location from the stored points, with no
call to the provider and no location sent anywhere. `changed_at` records when a point's forecast
last differed, which shows how often the provider really updates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.integrations.pollen.client import DEFAULT_BASE, PollenClient, PollenError

log = logging.getLogger("wa.pollen")

# Rough outlines (lat, lon) of Great Britain and Northern Ireland: enough to leave the sea out of
# the lattice, not for navigation. Islands are covered by their nearest mainland point.
_GB = [
    (50.0, -5.75), (50.15, -5.8), (50.3, -5.4), (50.2, -5.0), (50.7, -4.6), (51.2, -4.2), (51.2, -3.5), (51.5, -3.2), (51.7, -5.3),
    (52.0, -5.2), (52.8, -4.7), (53.3, -4.6), (53.4, -3.1), (54.2, -3.2), (54.9, -3.3), (54.7, -5.0),
    (55.0, -5.1), (55.8, -5.8), (56.5, -5.7), (57.4, -5.8), (58.6, -5.0), (58.65, -3.1), (57.7, -4.0),
    (57.7, -3.0), (57.6, -1.8), (56.0, -2.6), (55.8, -2.0), (55.0, -1.4), (54.5, -0.6), (53.6, 0.1),
    (52.7, 1.8), (51.9, 1.35), (51.4, 1.45), (51.0, 1.4), (50.75, 0.3), (50.75, -1.0), (50.6, -2.0),
    (50.3, -3.6),
]  # fmt: skip
_NI = [(54.0, -6.0), (54.6, -5.5), (55.2, -6.0), (55.2, -7.0), (54.7, -8.0), (54.2, -7.3), (54.0, -6.4)]
SOURCE = "Austrian Pollen Information Service, www.polleninformation.at"
PAUSE_S = 10.0  # between provider calls: gentle, the service asks for fair use
MAX_FAILURES_IN_A_ROW = 4  # then give up until the next run
# A point the provider could not answer waits before it is tried first again: 2 days, then 4, 8 ... up to 30.
# Without it, points that never succeed stay "stalest" for ever, are tried first on every run, and stop the rest
# of the grid refreshing.
FAIL_BACKOFF_S, FAIL_BACKOFF_MAX_S = 2 * 86400, 30 * 86400


def fail_key(lat: float, lon: float) -> str:
    return f"wa:pollen:gridfail:{lat}:{lon}"


def fail_count_key(lat: float, lon: float) -> str:
    return f"wa:pollen:gridfails:{lat}:{lon}"


def backoff_s(failures: int) -> int:
    return int(min(FAIL_BACKOFF_S * 2 ** max(0, failures - 1), FAIL_BACKOFF_MAX_S))


def order(pts: list[tuple[float, float]], fetched: dict[tuple[float, float], datetime],
          failing: set[tuple[float, float]]) -> list[tuple[float, float]]:  # fmt: skip
    """Stalest first, but points that failed lately after every other point."""
    oldest = datetime.min.replace(tzinfo=UTC)
    return sorted(pts, key=lambda q: (q in failing, fetched.get(q) or oldest))
MAX_KM = 150.0  # and neither is one further than this from the request


def _inside(lat: float, lon: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i, (yi, xi) in enumerate(poly):
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def on_land(lat: float, lon: float) -> bool:
    return _inside(lat, lon, _GB) or _inside(lat, lon, _NI)


def grid_points(step: float = 1.0) -> list[tuple[float, float]]:
    """Cell centres of a lattice with the given spacing in degrees. A cell counts when any of nine
    spots inside it is on land, so coastal cells are kept."""
    step = max(0.25, min(3.0, step))
    pts: list[tuple[float, float]] = []
    lat = 49.75 + step / 2
    while lat < 59.0:
        lon = -8.5 + step / 2
        while lon < 2.0:
            spots = [(lat + dy * step / 3, lon + dx * step / 3) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
            if any(on_land(a, b) for a, b in spots):
                pts.append((round(lat, 3), round(lon, 3)))
            lon += step
        lat += step
    return pts


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def fingerprint(forecast: dict[str, Any]) -> str:
    keep = {k: forecast.get(k) for k in ("dates", "allergens", "allergy_risk")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:12]


_LISTS = ("dates", "allergy_risk", "allergy_risk_level", "allergy_risk_label", "allergy_risk_hourly")


def for_day(forecast: dict[str, Any], today: date) -> dict[str, Any] | None:
    """A stored forecast cut so that its first day is `today`, or None when it does not cover today.

    A fetch returns four days (today and three more), so a point fetched two days ago still has two
    useful days left. Reading by date lets points be refreshed every few days instead of every day."""
    dates = forecast.get("dates") or []
    try:
        offset = (today - date.fromisoformat(str(dates[0]))).days
    except (IndexError, ValueError):
        return None
    if offset < 0 or offset >= len(dates):
        return None
    out = {k: (v[offset:] if k in _LISTS and isinstance(v, list) else v) for k, v in forecast.items()}
    out["allergens"] = [
        {**a, "levels": (a.get("levels") or [])[offset:], "labels": (a.get("labels") or [])[offset:]}
        for a in forecast.get("allergens") or []
    ]
    return out


def _today(now: datetime | None) -> date:
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(get_settings().local_tz)).date()


def current(points: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """The stored points with each forecast cut to start today; points that no longer cover today are left out."""
    today = _today(now)
    out = []
    for p in points:
        f = for_day(p["forecast"], today)
        if f is not None:
            out.append({**p, "forecast": f})
    return out


def nearest(
    points: list[dict[str, Any]], lat: float, lon: float, now: datetime | None = None
) -> tuple[dict[str, Any], float] | None:
    """The closest stored point that still covers today and is near enough, with its distance in km."""
    best: tuple[dict[str, Any], float] | None = None
    for p in current(points, now):
        d = km((lat, lon), (p["lat"], p["lon"]))
        if d <= MAX_KM and (best is None or d < best[1]):
            best = (p, d)
    return best


async def load(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT lat, lon, forecast, fetched_at, changed_at FROM pollen_grid"))).mappings().all()
    return [dict(r) for r in rows]


async def store(engine: AsyncEngine, lat: float, lon: float, forecast: dict[str, Any]) -> None:
    """Save a point's forecast; changed_at moves only when the forecast differs from the last one."""
    clean = {k: v for k, v in forecast.items() if k != "allergy_risk_hourly"}
    fp = fingerprint(clean)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO pollen_grid (lat, lon, forecast, fingerprint, fetched_at, changed_at)
                VALUES (:lat, :lon, CAST(:f AS jsonb), :fp, now(), now())
                ON CONFLICT (lat, lon) DO UPDATE SET
                  forecast = EXCLUDED.forecast, fetched_at = now(),
                  changed_at = CASE WHEN pollen_grid.fingerprint = EXCLUDED.fingerprint
                                    THEN pollen_grid.changed_at ELSE now() END,
                  fingerprint = EXCLUDED.fingerprint
                """
            ),
            {"lat": lat, "lon": lon, "f": json.dumps(clean), "fp": fp},
        )


async def prune(engine: AsyncEngine, keep: set[tuple[float, float]]) -> None:
    """Drop points that are no longer on the lattice (after a change of spacing)."""
    async with engine.connect() as conn:
        have = {(r[0], r[1]) for r in (await conn.execute(text("SELECT lat, lon FROM pollen_grid"))).all()}
    stale = have - keep
    if stale:
        async with engine.begin() as conn:
            for lat, lon in stale:
                await conn.execute(text("DELETE FROM pollen_grid WHERE lat = :a AND lon = :b"), {"a": lat, "b": lon})


async def pollen_grid_sync(ctx: dict[str, Any], pause: float = PAUSE_S) -> Any:
    """Worker job: refresh the stalest lattice points, a few each run. A forecast covers four days, so
    every point being refreshed every couple of days is enough, and it keeps within the daily budget."""
    from app.jobs import tracked
    from app.pollen_budget import PollenBudgetError, PollenLimiter

    s = get_settings()

    async def run() -> Any:
        if not s.pollen_api_key:
            return "no key"
        engine: AsyncEngine = ctx["engine"]
        today = datetime.now(ZoneInfo(s.local_tz)).date()
        pts = grid_points(s.pollen_grid_step_deg)
        limiter = PollenLimiter(ctx["redis"], s.pollen_daily_limit)
        have = {(p["lat"], p["lon"]): p["fetched_at"] for p in await load(engine)}
        redis = ctx["redis"]
        flags = await redis.mget([fail_key(a, b) for a, b in pts]) if pts else []
        failing = {q for q, f in zip(pts, flags, strict=True) if f}
        pts = order(pts, have, failing)
        ok = failed = streak = 0
        failed_now: list[str] = []
        budget = False
        async with PollenClient(s.pollen_api_key, s.pollen_base_url or DEFAULT_BASE, limiter=limiter) as c:
            for lat, lon in pts:
                if streak >= MAX_FAILURES_IN_A_ROW:
                    break  # the service is refusing us; try again next run
                if ok + failed >= s.pollen_grid_calls_per_run or await limiter.remaining() <= s.pollen_reserve_calls:
                    budget = True  # leave the rest of the day's budget for saved places and look-ups
                    break
                await asyncio.sleep(pause)
                try:
                    await store(engine, lat, lon, await c.forecast(lat, lon, today, country=s.pollen_country))
                    ok += 1
                    streak = 0
                    if (lat, lon) in failing:
                        await redis.delete(fail_key(lat, lon), fail_count_key(lat, lon))
                except PollenBudgetError:
                    budget = True
                    break
                except PollenError as exc:
                    failed += 1
                    # In a row only counts points that have worked before: those failing means the service is
                    # refusing us. A point that never had data failing says nothing about the service.
                    streak = streak + 1 if (lat, lon) in have else streak
                    failed_now.append(f"{lat},{lon}")
                    n = await redis.incr(fail_count_key(lat, lon))
                    await redis.expire(fail_count_key(lat, lon), 60 * 86400)
                    await redis.set(fail_key(lat, lon), str(exc)[:200], ex=backoff_s(n))
                    log.warning("pollen grid point (%s, %s) failed (%d time(s)): %s", lat, lon, n, exc)
        if ok:
            await prune(engine, set(pts))
        return {"points": len(pts), "ok": ok, "failed": failed, "gave_up": streak >= MAX_FAILURES_IN_A_ROW,
                "budget_stop": budget, "with_data": len(have), "waiting_after_failing": len(failing),
                "failed_now": failed_now[:20]}  # fmt: skip

    return await tracked(ctx["engine"], "pollen_grid_sync", run)


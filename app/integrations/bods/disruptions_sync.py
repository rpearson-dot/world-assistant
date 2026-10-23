"""Keep the bus disruptions that are near your places (BODS SIRI-SX), every 10 minutes.

The national file holds every publishing authority's disruptions (about 6 MB). Only the ones that are current and
have a stop or place within `bus_disruptions_km` of a saved place are kept, and the stored set is replaced each
time, so one that ends or is withdrawn disappears. Off unless `WA_BUS_DISRUPTIONS_POLL_ENABLED=true`.

    python -m app.integrations.bods.disruptions_sync
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.bods.client import BodsError
from app.integrations.bods.disruptions import Situation, fetch, km, parse_situations
from app.integrations.bus import store as bus_store
from app.jobs import tracked

log = logging.getLogger("wa.busdisruptions")

INSERT = text(
    """
    INSERT INTO bus_disruption (id, publisher, summary, description, reason, planned, starts_at, ends_at, lines,
      operators, stops, near_place, distance_km, lat, lon, seen)
    VALUES (:id, :publisher, :summary, :description, :reason, :planned, :starts_at, :ends_at, :lines, :operators,
      :stops, :near_place, :distance_km, :lat, :lon, :seen)
    """
)


def _when(value: str) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def current(s: Situation, now: datetime, stale_days: int) -> bool:
    """Not closed, not ended, and not an open-ended one left over from long ago (some publishers never close them)."""
    if s.progress.lower() == "closed":
        return False
    starts, ends = _when(s.starts), _when(s.ends)
    if ends is not None:
        return ends >= now
    return starts is None or starts >= now - timedelta(days=stale_days)


def select(
    found: list[Situation], places: list[tuple[str, float, float]], coords: dict[str, tuple[float, float]],
    now: datetime, within_km: float, stale_days: int,
) -> list[dict[str, Any]]:
    """The rows to keep: current, and with a stop or place within `within_km` of a saved place."""
    rows: dict[str, dict[str, Any]] = {}
    for s in found:
        if not current(s, now, stale_days):
            continue
        points = s.places + [coords[sid] for sid in s.stops if sid in coords]
        best = min(((km((plat, plon), p), pid, p) for pid, plat, plon in places for p in points), default=None)
        if best is None or best[0] > within_km:
            continue
        dist, pid, (lat, lon) = best
        rid = f"{s.publisher or 'unknown'}:{s.number or s.summary[:60]}"
        rows[rid] = {"id": rid, "publisher": s.publisher or "", "summary": s.summary, "description": s.description,
                     "reason": s.reason or None, "planned": s.planned, "starts_at": _when(s.starts),
                     "ends_at": _when(s.ends), "lines": s.lines, "operators": s.operators, "stops": s.stops,
                     "near_place": pid, "distance_km": round(dist, 2), "lat": lat, "lon": lon, "seen": now}  # fmt: skip
    return list(rows.values())


async def sync(engine: AsyncEngine, api_key: str, now: datetime | None = None) -> dict[str, Any]:
    s = get_settings()
    now = now or datetime.now(UTC)
    _, body, _ = await fetch(api_key)
    found = parse_situations(body)
    async with engine.connect() as conn:
        places = [(r[0], float(r[1]), float(r[2])) for r in await conn.execute(text("SELECT id, lat, lon FROM place"))]
        coords = await bus_store.stop_coords(conn, sorted({sid for x in found for sid in x.stops}))
    if not places:
        return {"read": len(found), "kept": 0, "note": "no saved places"}
    rows = select(found, places, coords, now, s.bus_disruptions_km, s.bus_disruptions_stale_days)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM bus_disruption"))
        if rows:
            await conn.execute(INSERT, rows)
    publishers = sorted({x.publisher for x in found if x.publisher})
    return {"read": len(found), "kept": len(rows), "publishers": len(publishers)}


async def bus_disruptions_poll(ctx: dict[str, Any]) -> Any:
    """arq job."""

    async def run() -> Any:
        s = get_settings()
        if not s.bus_disruptions_poll_enabled:
            return "disabled"
        if not s.bods_api_key:
            return "no bods_api_key"
        return await sync(ctx["engine"], s.bods_api_key)

    return await tracked(ctx["engine"], "bus_disruptions", run)


async def _cli() -> int:
    s = get_settings()
    engine = create_async_engine(s.database_url, pool_pre_ping=True)
    try:
        print(await sync(engine, s.bods_api_key))
        return 0
    except BodsError as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))

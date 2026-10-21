"""Record the day's successful runs from the tracker's stored state, for a day the live matching missed.

    docker compose exec worker python -m app.integrations.bus.backfill 213 [YYYY-MM-DD]

Reads the buses the tracker still remembers (its last 40 trips and the live ones), works out which
reached the target in time using the same rule the poller uses, and saves the ones not already saved.
Safe to repeat: a bus already on record is skipped. It changes nothing else, not even the stored state.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.bus import store
from app.integrations.bus.config import TrackerConfig
from app.integrations.bus.poller import compute_hits


async def backfill(engine: AsyncEngine, tid: str, day: date | None = None, now: float | None = None) -> dict[str, Any]:
    tz = ZoneInfo(get_settings().local_tz)
    the_day = day or datetime.now(tz).date()
    async with engine.connect() as conn:
        row = await store.get_tracker(conn, tid)
        if not row:
            return {"ok": False, "message": f"no tracker {tid!r}"}
        found = (await conn.execute(
            text("SELECT slots FROM bus_target_day WHERE tracker = :t AND day = :d"), {"t": tid, "d": the_day})).first()  # fmt: skip
    cfg = TrackerConfig.model_validate(row["config"])
    if not cfg.target:
        return {"ok": False, "message": "this tracker has no target"}
    slots = list(found[0]) if found else []
    state = {**(row["state"] or {}), "reported": [], "terminal": {}}  # a copy: the stored state is not saved back
    hits = compute_hits(cfg, state, now or time.time(), the_day, tz, slots)
    async with engine.begin() as conn:
        await store.ensure_day(conn, tid, the_day, slots)
        await store.save_hits(conn, tid, the_day, hits)
    return {"ok": True, "day": the_day.isoformat(), "found": len(hits),
            "buses": [{"vehicle": h["vehicle"], "slot": h["slot"],
                       "arrived": datetime.fromtimestamp(h["arrive_ts"], tz).strftime("%H:%M")} for h in hits]}  # fmt: skip


async def _cli(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m app.integrations.bus.backfill <tracker> [YYYY-MM-DD]")
        return 2
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        out = await backfill(engine, argv[0], date.fromisoformat(argv[1]) if len(argv) > 1 else None)
        print(out)
        return 0 if out["ok"] else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli(sys.argv[1:])))

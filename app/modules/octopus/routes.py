"""Electricity prices for Home Assistant (and anything else) to act on. Read-only: nothing here controls a device."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_token
from app.modules.octopus import sessions as sess
from app.modules.octopus import store, summary

router = APIRouter(prefix="/v1/energy", tags=["energy"], dependencies=[Depends(require_token)])


@router.get("/now")
async def now(
    request: Request,
    hours: float = Query(3.0, ge=0.5, le=12, description="length of the cheapest block"),
) -> dict[str, Any]:
    """The current rate and band, the next change, the next cheap and peak windows, and the cheapest block."""
    tariff, periods = await store.load(request.app.state.engine)
    now_ = datetime.now(UTC)
    out = summary.summarise(tariff, periods, now_, hours)
    out["sessions"] = sess.summarise(await store.load_sessions(request.app.state.engine), now_)
    return out


@router.get("/sessions")
async def sessions(request: Request) -> dict[str, Any]:
    """Saving Sessions (with whether you joined) and Octopus boost events: on now, next, upcoming."""
    return sess.summarise(await store.load_sessions(request.app.state.engine), datetime.now(UTC))


@router.get("/rates")
async def rates(request: Request, hours: float = Query(48, ge=1, le=96)) -> dict[str, Any]:
    """The upcoming rates, each with its band."""
    tariff, periods = await store.load(request.app.state.engine)
    return {"tariff": tariff, "slots": summary.slots(periods, datetime.now(UTC), hours)}


@router.get("/schedule")
async def schedule(request: Request) -> dict[str, Any]:
    """The inverter's six daily slots for this tariff: start times, which charge from the grid, and what each charging
    slot must cover until the next cheap window (for the Home Assistant energy plan)."""
    from zoneinfo import ZoneInfo

    from app.config import get_settings
    from app.modules.octopus import schedule as S

    tariff, periods = await store.load(request.app.state.engine)
    now_ = datetime.now(UTC)
    summed = sess.summarise(await store.load_sessions(request.app.state.engine), now_)
    return {"tariff": tariff, "computed_at": now_.isoformat(),
            **S.build(periods, now_, ZoneInfo(get_settings().local_tz), summed["saving"].get("upcoming") or [],
                      [x for x in [summed["free"].get("current")] if x] + (summed["free"].get("upcoming") or []))}  # fmt: skip


@router.get("/windows")
async def windows(request: Request, hours: float = Query(3.0, ge=0.5, le=12)) -> dict[str, Any]:
    """Cheapest block of `hours` in the next 24 hours, and the banded windows."""
    from app.modules.octopus import rates as R

    tariff, periods = await store.load(request.app.state.engine)
    now_ = datetime.now(UTC)
    block = R.cheapest_block(periods, hours, now_)
    wins = [w for w in R.windows(periods) if w.end > now_]
    return {"tariff": tariff, "cheapest_block": summary._window(block),
            "windows": [summary._window(w) for w in wins]}  # fmt: skip


@router.get("/usage")
async def usage(request: Request, days: int = Query(30, ge=1, le=400)) -> dict[str, Any]:
    """Each day from the meter (Octopus, half-hourly): kWh bought and sold, what was bought cost at your rates, and how
    much was bought 16:00 to 19:00. Octopus publishes a day's readings the next day."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from sqlalchemy import text

    from app.config import get_settings
    from app.modules.octopus import usage as U

    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    async with request.app.state.engine.connect() as conn:
        rows = [dict(r) for r in (await conn.execute(text(
            "SELECT direction, interval_start AS start, kwh FROM energy_usage WHERE interval_start >= :a ORDER BY interval_start"),
            {"a": start})).mappings().all()]  # fmt: skip
        rates = await U.rates_between(conn, start, end)
        last = (await conn.execute(text("SELECT max(interval_start) FROM energy_usage"))).scalar()
    out = U.days(rows, rates, ZoneInfo(get_settings().local_tz))
    return {"days": out, "latest_reading": last,
            "totals": {k: round(sum(d[k] for d in out), 1) for k in ("import_kwh", "export_kwh", "dear_kwh")} |
                      {"cost_pounds": round(sum(d["cost_p"] for d in out) / 100, 2)}}  # fmt: skip

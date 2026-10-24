"""Electricity prices for Home Assistant (and anything else) to act on. Read-only: nothing here controls a device."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_token
from app.energy import sessions as sess
from app.energy import store, summary

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
    from app.energy import schedule as S

    tariff, periods = await store.load(request.app.state.engine)
    now_ = datetime.now(UTC)
    summed = sess.summarise(await store.load_sessions(request.app.state.engine), now_)
    return {"tariff": tariff, "computed_at": now_.isoformat(),
            **S.build(periods, now_, ZoneInfo(get_settings().local_tz), summed["saving"].get("upcoming") or [],
                      summed["free"].get("upcoming") or [])}  # fmt: skip


@router.get("/windows")
async def windows(request: Request, hours: float = Query(3.0, ge=0.5, le=12)) -> dict[str, Any]:
    """Cheapest block of `hours` in the next 24 hours, and the banded windows."""
    from app.energy import rates as R

    tariff, periods = await store.load(request.app.state.engine)
    now_ = datetime.now(UTC)
    block = R.cheapest_block(periods, hours, now_)
    wins = [w for w in R.windows(periods) if w.end > now_]
    return {"tariff": tariff, "cheapest_block": summary._window(block),
            "windows": [summary._window(w) for w in wins]}  # fmt: skip

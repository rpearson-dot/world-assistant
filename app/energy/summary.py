"""The answer Home Assistant wants: now, next, and the windows ahead. Pure over a list of periods."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.energy import rates as R


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _window(w: R.Window | None) -> dict[str, Any] | None:
    if w is None:
        return None
    return {"band": w.band, "start": _iso(w.start), "end": _iso(w.end), "hours": round(w.hours, 2),
            "pence": w.pence}  # fmt: skip


def summarise(
    tariff: str | None, periods: list[R.Period], now: datetime, hours: float = 3.0
) -> dict[str, Any]:
    here = R.current(periods, now)
    if tariff is None or here is None:
        return {"tariff": tariff, "available": False, "now": None}
    banded = {id(p): b for p, b in R.band_of(periods)}
    wins = R.windows(periods)
    change = R.next_change(periods, now)
    nxt = R.current(periods, change[0]) if change else None
    cur_win = next((w for w in wins if w.start <= now < w.end), None)
    cheap = R.next_window(wins, R.CHEAP, now)
    peak = R.next_window(wins, R.PEAK, now)
    block = R.cheapest_block(periods, hours, now)
    mins = round((change[0] - now).total_seconds() / 60) if change else None
    return {
        "tariff": tariff,
        "available": True,
        "now": {"pence": here.pence, "band": banded[id(here)], "until": _iso(cur_win.end if cur_win else here.end)},
        "next": None if not change or nxt is None else
        {"pence": nxt.pence, "band": banded[id(nxt)], "at": _iso(change[0]), "minutes": mins},
        "cheap_now": banded[id(here)] == R.CHEAP,
        "next_cheap": _window(cheap),
        "next_peak": _window(peak),
        "cheapest_block": {**(_window(block) or {}), "requested_hours": hours} if block else None,
        "covers_until": _iso(periods[-1].end),
        "average_pence": round(sum(p.pence * (p.end - p.start).total_seconds() for p in periods
                                   if p.end > now and p.start < now + timedelta(hours=24))
                               / max(1.0, sum((min(p.end, now + timedelta(hours=24)) - max(p.start, now)).total_seconds()
                                              for p in periods if p.end > now and p.start < now + timedelta(hours=24))), 3),
    }  # fmt: skip


def slots(periods: list[R.Period], now: datetime, hours: float = 48.0) -> list[dict[str, Any]]:
    horizon = now + timedelta(hours=hours)
    banded = R.band_of(periods)
    return [{"start": _iso(p.start), "end": _iso(p.end), "pence": p.pence, "band": b}
            for p, b in banded if p.end > now and p.start < horizon]  # fmt: skip

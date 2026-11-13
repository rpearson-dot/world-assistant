"""The inverter's six time slots, worked out from the tariff. Pure.

A Deye/Sunsynk-style inverter repeats six slots every day: each has a start time, a grid-charge switch and a target
charge level. This turns the real rates into that shape for any tariff (Cosy, Go, Flux, Agile...):

1. Each half hour of the clock day is priced at its next occurrence from now (a rate not published yet is taken
   from the same time a day, two days or a week earlier, which is right for a fixed time-of-use tariff).
2. Those 48 prices are banded the same way as everywhere else (app.modules.octopus.rates), and the cheap half hours are
   joined into windows around the clock (22:00-00:00 and 00:00-02:00 are one window).
3. At most three windows are kept, the cheapest first: six slots hold three charge windows and the gaps after them.
4. Slots start at each window's start (grid charge on) and end (off); a slot at midnight is added when there is
   room, and the longest slots are split until there are six.

A Free Electricity hour you joined, in the next 24 hours, gets slots of its own (grid charge on, "free": true) when
they fit in the six: inside or next to a cheap window it is split off it (one more slot), elsewhere it needs two.
When they do not fit (Cosy's three windows fill all six), it is left out, and Home Assistant borrows the slot it falls
in for the hour, as before.

Each charging slot also says what it must cover: the time from the end of its window to the start of the next
one, as hours of night (00:00-07:00), day (07:00-16:00) and evening (16:00-24:00) load, and the share of a day's
solar that falls in it, today and tomorrow. Home Assistant turns that into a target with the house's own numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.modules.octopus import rates as R

CELLS = 48  # half hours in a clock day
STEP = timedelta(minutes=30)
SLOTS = 6
MAX_WINDOWS = 3
LOOK_BACK = (timedelta(days=1), timedelta(days=2), timedelta(days=7))
NIGHT_END, DAY_END = 7, 16  # load bands by clock hour: night 00-07, day 07-16, evening 16-24


def _clock(cell: int) -> str:
    cell %= CELLS
    return f"{cell // 2:02d}:{30 * (cell % 2):02d}"


def _solar_weights() -> list[float]:
    """A plain solar day: nothing before 07:00 or after 19:00, most at 13:00. Shares of the day's total."""
    raw = [max(0.0, 6.0 - abs(c / 2 + 0.25 - 13.0)) for c in range(CELLS)]
    total = sum(raw)
    return [r / total for r in raw]


SOLAR = _solar_weights()


@dataclass
class Run:
    start: int  # cell
    length: int  # cells
    pence: float
    free: bool = False  # a joined Free Electricity hour

    @property
    def end(self) -> int:
        return (self.start + self.length) % CELLS


def _price_at(periods: list[R.Period], at: datetime) -> float | None:
    for back in (timedelta(0), *LOOK_BACK):
        p = R.current(periods, at - back)
        if p is not None:
            return p.pence
    return None


def _occurrences(now: datetime, tz: ZoneInfo) -> list[datetime]:
    """Each half hour of the clock day at its next occurrence, the current half hour counting as now."""
    local = now.astimezone(tz)
    floor = local.replace(minute=0 if local.minute < 30 else 30, second=0, microsecond=0)
    midnight = floor.replace(hour=0, minute=0)
    out = []
    for c in range(CELLS):
        t = (midnight + c * STEP).replace(tzinfo=None).replace(tzinfo=tz)  # wall clock, whatever the offset
        if t < floor:
            t = (midnight + timedelta(days=1) + c * STEP).replace(tzinfo=None).replace(tzinfo=tz)
        out.append(t)
    return out


def _runs(cheap: list[bool], prices: list[float]) -> list[Run]:
    """Runs of cheap half hours around the clock (a run may cross midnight)."""
    if all(cheap) or not any(cheap):
        return []
    first = next(c for c in range(CELLS) if not cheap[c])  # start the walk on an expensive half hour
    runs: list[Run] = []
    for k in range(1, CELLS + 1):
        c = (first + k) % CELLS
        if cheap[c] and not cheap[(c - 1) % CELLS]:
            runs.append(Run(c, 0, 0.0))
        if cheap[c]:
            runs[-1].length += 1
    for r in runs:
        cells = [(r.start + i) % CELLS for i in range(r.length)]
        r.pence = round(sum(prices[c] for c in cells) / len(cells), 3)
    return runs


def _with_free(runs: list[Run], free: list[bool], prices: list[float]) -> list[Run]:
    """The windows with the free half hours split out as runs of their own (cheap windows lose those cells)."""
    label = [0] * CELLS
    for r in runs:
        for i in range(r.length):
            label[(r.start + i) % CELLS] = 1
    for c in range(CELLS):
        if free[c]:
            label[c] = 2
    if all(label) and len(set(label)) == 1:
        return runs
    first = next((c for c in range(CELLS) if label[c] != label[(c - 1) % CELLS]), 0)
    out: list[Run] = []
    for k in range(CELLS):
        c = (first + k) % CELLS
        if not label[c]:
            continue
        if not out or label[(c - 1) % CELLS] != label[c] or k == 0:
            out.append(Run(c, 0, 0.0, free=label[c] == 2))
        out[-1].length += 1
    for r in out:
        cells = [(r.start + i) % CELLS for i in range(r.length)]
        r.pence = round(sum(prices[c] for c in cells) / len(cells), 3)
    return sorted(out, key=lambda r: r.start)


def _edges(runs: list[Run]) -> int:
    return len({r.start for r in runs} | {r.end for r in runs})


def _boundaries(runs: list[Run]) -> dict[int, bool]:
    """Slot starts (cell -> grid charge on) for these windows, exactly six of them."""
    b: dict[int, bool] = {}
    for r in runs:
        b.setdefault(r.end, False)
    for r in runs:
        b[r.start] = True
    if not b:
        b = {0: False}

    def state_at(cell: int) -> bool:
        before = max((s for s in b if s <= cell), default=max(b))
        return b[before]

    if 0 not in b and len(b) < SLOTS:
        b[0] = state_at(0)
    while len(b) < SLOTS:  # split the longest slot in two
        starts = sorted(b)
        lengths = [((starts[(i + 1) % len(starts)] - s) % CELLS) or CELLS for i, s in enumerate(starts)]
        i = max(range(len(starts)), key=lambda j: lengths[j])
        if lengths[i] < 2:
            break
        b[(starts[i] + lengths[i] // 2) % CELLS] = b[starts[i]]
    return b


def _joined(rows: list[dict[str, Any]] | None) -> list[datetime]:
    return sorted(datetime.fromisoformat(r["start"]) for r in rows or [] if r.get("joined") and r.get("start"))


def build(periods: list[R.Period], now: datetime, tz: ZoneInfo, sessions: list[dict[str, Any]] | None = None,
          free: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    """The six slots for these rates. `sessions`: Saving Sessions, `free`: Free Electricity hours ({start, end,
    joined}); only joined ones count. A charging slot's cover stops at a joined free hour: that is free anyway."""
    when = _occurrences(now, tz)
    found = [_price_at(periods, t) for t in when]
    known = [p for p in found if p is not None]
    if len(known) < CELLS * 3 // 4:
        return {"available": False, "reason": "not enough rates to plan a day", "slots": [], "windows": [],
                "warnings": []}  # fmt: skip
    fill = sorted(known)[len(known) // 2]
    prices = [p if p is not None else fill for p in found]
    band = R.band_function([R.Period(t, t + STEP, p) for t, p in zip(when, prices, strict=True)])
    cheap = [band(p) == R.CHEAP for p in prices]
    runs = sorted(_runs(cheap, prices), key=lambda r: (r.pence, -r.length))[:MAX_WINDOWS]
    runs.sort(key=lambda r: r.start)
    free_at = _joined(free)
    spans = [(datetime.fromisoformat(f["start"]), datetime.fromisoformat(f["end"])) for f in free or []
             if f.get("joined") and f.get("start") and f.get("end")]  # fmt: skip
    free_cells = [any(a <= when[c] < b for a, b in spans) for c in range(CELLS)]
    left_out: list[str] = []
    if any(free_cells):
        split = _with_free(runs, free_cells, [0.0 if f else p for f, p in zip(free_cells, prices, strict=True)])
        if _edges(split) <= SLOTS:
            runs = split
            prices = [0.0 if f else p for f, p in zip(free_cells, prices, strict=True)]
        else:
            left_out = sorted({a.astimezone(tz).strftime("%a %H:%M") for a, b in spans
                               if any(a <= when[c] < b for c in range(CELLS))})  # fmt: skip
    bounds = _boundaries(runs)
    starts = sorted(bounds)
    first = 0 if 0 in bounds else starts[0]
    starts = sorted(starts, key=lambda s: (s - first) % CELLS)

    joined = _joined(sessions)
    warnings: list[str] = []
    slots: list[dict[str, Any]] = []
    today = now.astimezone(tz).date()
    for n, s in enumerate(starts, 1):
        e = starts[n % len(starts)]
        length = ((e - s) % CELLS) or CELLS
        cells = [(s + i) % CELLS for i in range(length)]
        slot: dict[str, Any] = {"n": n, "start": _clock(s), "end": _clock(e), "charge": bounds[s],
                                "pence": round(sum(prices[c] for c in cells) / len(cells), 3),
                                "next_start": when[s].isoformat()}  # fmt: skip
        if bounds[s]:
            run = next(r for r in runs if (s - r.start) % CELLS < r.length)
            nxt = min(runs, key=lambda r: ((r.start - run.end) % CELLS) or CELLS)  # the next window (a free one too)
            cover = ((nxt.start - run.end) % CELLS) or CELLS
            if nxt is run:
                cover = CELLS - run.length
            run_start = when[run.start]
            if run_start - timedelta(days=1) + run.length * STEP > now:  # this window is under way now
                run_start -= timedelta(days=1)
            run_end = run_start + run.length * STEP
            until_free = next((f for f in free_at if run_end <= f < run_end + cover * STEP), None)
            if until_free is not None:  # only up to the free hour
                cover = max(0, round((until_free - run_end) / STEP))
            load = {"night": 0.0, "day": 0.0, "evening": 0.0}
            solar = {"today": 0.0, "tomorrow": 0.0}
            for j in range(cover):
                c = (run.end + j) % CELLS
                hour = c // 2
                load["night" if hour < NIGHT_END else "day" if hour < DAY_END else "evening"] += 0.5
                at = (run_end + j * STEP).astimezone(tz)
                solar["today" if at.date() == today else "tomorrow"] += SOLAR[c]
            cover_end = run_end + cover * STEP
            session = any(run_end <= st < cover_end for st in joined)
            for st in joined:
                if run_start <= st < run_end:
                    warnings.append(f"A Saving Session you joined at {st.astimezone(tz):%a %H:%M} falls in charging "
                                    f"slot {n} ({_clock(s)}-{_clock(e)}): the battery would charge from the grid "
                                    "during it.")  # fmt: skip
            slot.update({"window": runs.index(run) + 1, "window_hours": run.length / 2,
                         "cover_from": _clock(run.end), "cover_to": _clock(run.end + cover), "cover_hours": cover / 2,
                         "until_free": until_free.isoformat() if until_free else None,
                         "load_hours": load, "solar_share": {k: round(v, 4) for k, v in solar.items()},
                         "session": session, "window_pence": run.pence, "free": run.free})  # fmt: skip
        slots.append(slot)
    wins = [{"start": _clock(r.start), "end": _clock(r.end), "pence": r.pence, "hours": r.length / 2, "free": r.free}
            for r in runs]  # fmt: skip
    return {"available": True, "slots": slots, "windows": wins, "warnings": list(dict.fromkeys(warnings)),
            "free_left_out": left_out,
            "summary": ", ".join(f"{w['start']}-{w['end']}" for w in wins) or "no cheap window"}  # fmt: skip

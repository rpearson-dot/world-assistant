"""What the rates mean: bands, windows and the cheapest block. Pure, so every rule is testable alone.

A rate period is `(start, end, pence)` with aware datetimes and `pence` in p/kWh including VAT. Octopus
returns periods of any length (a fixed time-of-use tariff such as Cosy gives three-hour blocks; Agile gives
half hours), so everything here works on periods and only slices them into half hours when it must.

Bands are worked out from the rates themselves, never from hard-coded times, so a changed tariff or a
different one (Agile, Go) still gets sensible answers:

* one distinct price: all `standard`;
* two: the lower is `cheap`, the higher `standard`;
* three: `cheap`, `standard`, `peak`;
* more (a tracker or Agile): a price is `cheap` at or below the 25th percentile, `peak` at or above the
  75th, `standard` between; a negative price is always `cheap`.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

CHEAP, STANDARD, PEAK = "cheap", "standard", "peak"
HALF_HOUR = timedelta(minutes=30)
DECIMALS = 3  # prices are compared to a tenth of a penny: floating-point noise is not a band


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    pence: float

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True)
class Window:
    """A run of back-to-back periods in one band."""

    band: str
    start: datetime
    end: datetime
    pence: float  # time-weighted average

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = fraction * (len(ordered) - 1)
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def band_function(periods: list[Period]) -> Any:
    """A function from a price to its band, fitted to these periods."""
    prices = sorted({round(p.pence, DECIMALS) for p in periods})
    if len(prices) <= 1:
        return lambda pence: STANDARD
    if len(prices) == 2:
        return lambda pence: CHEAP if round(pence, DECIMALS) <= prices[0] else STANDARD
    if len(prices) == 3:

        def three(pence: float) -> str:
            r = round(pence, DECIMALS)
            return CHEAP if r <= prices[0] else PEAK if r >= prices[2] else STANDARD

        return three
    # weight by duration, so a long standard stretch is not outvoted by a few short extremes
    weighted: list[float] = []
    for p in periods:
        weighted += [p.pence] * max(1, round((p.end - p.start) / HALF_HOUR))
    low, high = _percentile(weighted, 0.25), _percentile(weighted, 0.75)

    def many(pence: float) -> str:
        if pence < 0 or pence <= low:
            return CHEAP
        return PEAK if pence >= high else STANDARD

    return many


def band_of(periods: list[Period]) -> list[tuple[Period, str]]:
    fit = band_function(periods)
    return [(p, fit(p.pence)) for p in periods]


def normalise(periods: list[Period]) -> list[Period]:
    """Sorted, with overlaps resolved in favour of the later start (a corrected rate replaces the old)."""
    out: list[Period] = []
    for p in sorted(periods, key=lambda x: x.start):
        if p.end <= p.start:
            continue
        if out and p.start < out[-1].end:
            last = out[-1]
            out[-1] = Period(last.start, p.start, last.pence)
            if out[-1].end <= out[-1].start:
                out.pop()
        out.append(p)
    return out


def current(periods: list[Period], now: datetime) -> Period | None:
    return next((p for p in periods if p.contains(now)), None)


def windows(periods: list[Period]) -> list[Window]:
    """Back-to-back periods in the same band merged into windows. A gap in the data ends a window."""
    out: list[Window] = []
    run: list[tuple[Period, str]] = []

    def flush() -> None:
        if run:
            seconds = sum((p.end - p.start).total_seconds() for p, _ in run)
            avg = sum(p.pence * (p.end - p.start).total_seconds() for p, _ in run) / seconds
            out.append(Window(run[0][1], run[0][0].start, run[-1][0].end, round(avg, DECIMALS)))
            run.clear()

    for p, b in band_of(periods):
        if run and (run[-1][1] != b or run[-1][0].end != p.start):
            flush()
        run.append((p, b))
    flush()
    return out


def next_window(wins: list[Window], band: str, now: datetime) -> Window | None:
    """The window in `band` that is on now, or the next to start."""
    return next((w for w in wins if w.band == band and w.end > now), None)


def next_change(periods: list[Period], now: datetime) -> tuple[datetime, Period] | None:
    """When the band next changes, and the period it changes into."""
    banded = band_of(periods)
    here = next((b for p, b in banded if p.contains(now)), None)
    if here is None:
        return None
    for p, b in banded:
        if p.start > now and b != here:
            return p.start, p
    return None


def slice_half_hours(periods: list[Period]) -> list[Period]:
    out: list[Period] = []
    for p in periods:
        t = p.start
        while t < p.end:
            out.append(Period(t, min(t + HALF_HOUR, p.end), p.pence))
            t += HALF_HOUR
    return out


def cheapest_block(
    periods: list[Period], hours: float, now: datetime, within_hours: float = 24.0
) -> Window | None:
    """The cheapest run of `hours` of back-to-back time that starts from the half hour containing `now` and
    ends within `within_hours`. None when the data does not cover a block that long."""
    need = max(1, round(hours * 2))
    horizon = now + timedelta(hours=within_hours)
    slots = [s for s in slice_half_hours(periods) if s.end > now and s.start < horizon]
    # a block must be unbroken and end by the horizon
    best: tuple[float, int] | None = None
    for i in range(len(slots) - need + 1):
        block = slots[i : i + need]
        if block[-1].end > horizon or any(a.end != b.start for a, b in itertools.pairwise(block)):
            continue
        total = sum(s.pence for s in block)
        if best is None or total < best[0] - 1e-9:
            best = (total, i)
    if best is None:
        return None
    block = slots[best[1] : best[1] + need]
    return Window("block", block[0].start, block[-1].end, round(best[0] / need, DECIMALS))

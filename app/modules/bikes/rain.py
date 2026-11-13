"""Was a ride wet? A suggestion from the Environment Agency rain gauges along its route (from Garmin), or else the
nearest to where it started (or home), for the ride and the three hours before it (wet roads), which the rider
confirms or corrects on the page.

The gauges' readings go back about four weeks; an older ride gets no suggestion, and is simply asked about.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from app.modules.environment.sources.environment import clients as E

WET_MM = 1.0  # this much rain in the window: suggested wet
BEFORE = timedelta(hours=3)


async def _gauge(client: httpx.AsyncClient, lat: float, lon: float, start: datetime, end: datetime,
                 km: float = 15, skip: set[str] | None = None) -> tuple[float, str, float, str] | None:  # fmt: skip
    """The rain (mm) from `start` to `end` at the nearest gauge with readings within `km` (not one in `skip`): (mm,
    its name, how far, its reference); None when there is none."""
    for s in (await E._stations(client, lat, lon, km, parameter="rainfall"))[:2]:
        if skip and s["stationReference"] in skip:
            return None  # the nearest is one already counted
        m = next((m for m in E._measures(s) if "rainfall" in str(m.get("parameter", "")) or "rainfall" in str(m.get("@id", ""))), None)
        if not m or not m.get("@id"):
            m = next((m for m in E._measures(await E._detail(client, s["stationReference"])) if m.get("parameter") == "rainfall"), None)
        if not m or not m.get("@id"):
            continue
        got = [(t, v) for t, v in await E._recent(client, m["@id"], start) if t <= end + timedelta(minutes=15)]
        if not got:
            continue
        return round(sum(v for _, v in got), 1), s.get("label") or s["stationReference"], s["_km"], s["stationReference"]
    return None


async def suggest(client: httpx.AsyncClient, lat: float, lon: float, started: datetime, hours: float,
                  now: datetime) -> tuple[bool | None, str | None]:  # fmt: skip
    """(wet?, why) from the nearest rain gauge within 15 km; (None, why) when it cannot be told."""
    if now - started > timedelta(days=27):
        return None, "too long ago for the rain gauges' records"
    end = started + timedelta(hours=max(hours, 0.5))
    try:
        got = await _gauge(client, lat, lon, started - BEFORE, end)
    except E.EnvError as exc:
        return None, f"the rain gauges could not be asked ({exc})"
    if not got:
        return None, "no rain gauge with readings near enough"
    mm, where, km, _ = got
    return mm >= WET_MM, f"{mm} mm at {where} gauge ({km:.0f} km) during and before the ride"


def along(route: list[list[float]], every_km: float = 12.0, most: int = 8) -> list[tuple[float, float]]:
    """Points along a route (its start, then about every `every_km`), at most `most`, to ask the gauges near."""
    import itertools
    import math

    if not route:
        return []
    out = [(route[0][0], route[0][1])]
    since = 0.0
    for a, b in itertools.pairwise(route):
        since += math.hypot((b[0] - a[0]) * 111.32, (b[1] - a[1]) * 111.32 * math.cos(math.radians(a[0])))
        if since >= every_km:
            out.append((b[0], b[1]))
            since = 0.0
    if len(out) > most:
        step = (len(out) - 1) / (most - 1)
        out = [out[round(i * step)] for i in range(most)]
    return out


async def suggest_route(client: httpx.AsyncClient, route: list[list[float]], started: datetime, hours: float,
                        now: datetime) -> tuple[bool | None, str | None]:  # fmt: skip
    """(wet?, why) from the rain gauges along a ride's route (the nearest within 10 km of points about 12 km apart),
    each over the ride and the three hours before it: wet if any had as much as a wet ride's rain. A long ride can be
    dry at home and soaked 40 km out."""
    if now - started > timedelta(days=27):
        return None, "too long ago for the rain gauges' records"
    end = started + timedelta(hours=max(hours, 0.5))
    seen: set[str] = set()
    found: list[tuple[float, str]] = []
    try:
        for lat, lon in along(route):
            got = await _gauge(client, lat, lon, started - BEFORE, end, km=10, skip=seen)
            if got:
                seen.add(got[3])
                found.append((got[0], got[1]))
    except E.EnvError as exc:
        return None, f"the rain gauges could not be asked ({exc})"
    if not found:
        return None, "no rain gauge with readings near the route"
    mm, where = max(found)
    if len(found) == 1:
        return mm >= WET_MM, f"{mm} mm at {where} gauge on the route, during and before the ride"
    return mm >= WET_MM, f"up to {mm} mm along the route ({where}; {len(found)} gauges), during and before the ride"


def home_point(places: list[dict[str, Any]]) -> tuple[float, float] | None:
    home = next((p for p in places if p.get("kind") == "home" or p.get("id") == "home"), None) or (places[0] if places else None)
    return (float(home["lat"]), float(home["lon"])) if home else None


async def _probe(lat: float, lon: float) -> int:
    """For the live check: what the nearest gauge says for a two-hour ride yesterday morning."""
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Europe/London"))
    started = (now - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    async with httpx.AsyncClient(timeout=15) as client:
        wet, why = await suggest(client, lat, lon, started, 2.0, now)
    print(f"A 2 h ride from {started:%a %d %b %H:%M}: {'wet' if wet else 'dry' if wet is False else 'no suggestion'} ({why})")
    if wet is None:
        print("FAILED: no rain gauge reading")
        return 1
    return 0


if __name__ == "__main__":
    import asyncio
    import sys

    sys.exit(asyncio.run(_probe(float(sys.argv[1]), float(sys.argv[2]))))

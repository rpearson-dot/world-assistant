"""Was a ride wet? A suggestion from the nearest Environment Agency rain gauge to where it was (or home), for the ride
and the three hours before it (wet roads), which the rider confirms or corrects on the page.

The gauges' readings go back about four weeks; an older ride gets no suggestion, and is simply asked about.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from app.integrations.environment import clients as E

WET_MM = 1.0  # this much rain in the window: suggested wet
BEFORE = timedelta(hours=3)


async def suggest(client: httpx.AsyncClient, lat: float, lon: float, started: datetime, hours: float,
                  now: datetime) -> tuple[bool | None, str | None]:  # fmt: skip
    """(wet?, why) from the nearest rain gauge within 15 km; (None, why) when it cannot be told."""
    if now - started > timedelta(days=27):
        return None, "too long ago for the rain gauges' records"
    end = started + timedelta(hours=max(hours, 0.5))
    try:
        for s in (await E._stations(client, lat, lon, 15, parameter="rainfall"))[:2]:
            m = next((m for m in E._measures(s) if "rainfall" in str(m.get("parameter", "")) or "rainfall" in str(m.get("@id", ""))), None)
            if not m or not m.get("@id"):
                m = next((m for m in E._measures(await E._detail(client, s["stationReference"])) if m.get("parameter") == "rainfall"), None)
            if not m or not m.get("@id"):
                continue
            got = [(t, v) for t, v in await E._recent(client, m["@id"], started - BEFORE) if t <= end + timedelta(minutes=15)]
            if not got:
                continue
            mm = round(sum(v for _, v in got), 1)
            where = s.get("label") or s["stationReference"]
            return mm >= WET_MM, f"{mm} mm at {where} gauge ({s['_km']:.0f} km) during and before the ride"
    except E.EnvError as exc:
        return None, f"the rain gauges could not be asked ({exc})"
    return None, "no rain gauge with readings near enough"


def home_point(places: list[dict[str, Any]]) -> tuple[float, float] | None:
    home = next((p for p in places if p.get("kind") == "home" or p.get("id") == "home"), None) or (places[0] if places else None)
    return (float(home["lat"]), float(home["lon"])) if home else None

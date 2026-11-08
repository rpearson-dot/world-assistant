"""Event listings put in the wrong place: a provider's map position that disagrees with the venue's own postcode.

Ticketmaster in particular sometimes gives a venue the position of somewhere else (a Swansea show placed near home),
so it turns up in a search miles from where it is. Each listing with a postcode is checked against the local
Code-Point Open table: one more than MISPLACED_KM from its postcode is moved to the postcode, its distance worked out
again, and dropped if that puts it outside the search. Without the table (not built yet), or for a postcode it does not
know, a listing is left as it came.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.integrations.fuelfinder.queries import haversine_km

log = logging.getLogger("wa.events")

MISPLACED_KM = 10.0  # a venue's postcode centre is within a few hundred metres of it; 10 km is plainly somewhere else
_PC = re.compile(r"[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}")


def _compact(v: Any) -> str | None:
    pc = re.sub(r"\s+", "", str(v or "")).upper()
    return pc if _PC.fullmatch(pc) else None


async def postcode_points(engine: Any, postcodes: set[str]) -> dict[str, tuple[float, float]] | None:
    """Where each postcode is, from the local table; None when the table cannot be read."""
    if not postcodes or engine is None:
        return {}
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT pc, lat, lon FROM postcode WHERE pc = ANY(:pcs)"),
                                       {"pcs": sorted(postcodes)})).all()  # fmt: skip
    except (DBAPIError, AttributeError, OSError) as exc:  # the check is optional: a search goes on without it
        log.warning("the postcode table is not available (%s): event positions are not checked", type(exc).__name__)
        return None
    return {r.pc: (float(r.lat), float(r.lon)) for r in rows}


def correct(events: list[dict[str, Any]], points: dict[str, tuple[float, float]],
            origin: tuple[float, float] | None = None, radius_km: float | None = None) -> tuple[list[dict[str, Any]], int, int]:  # fmt: skip
    """The events with misplaced ones moved to their postcode (and given a `moved` note), those then outside radius_km
    of origin left out; and how many were moved and dropped."""
    out, moved, dropped = [], 0, 0
    for e in events:
        pc = _compact(e.get("postcode"))
        at = points.get(pc) if pc else None
        if at is None:
            out.append(e)
            continue
        if e.get("lat") is None or e.get("lon") is None:
            e = {**e, "lat": at[0], "lon": at[1]}
        else:
            off = haversine_km((float(e["lat"]), float(e["lon"])), at)
            if off <= MISPLACED_KM:
                out.append(e)
                continue
            moved += 1
            e = {**e, "lat": at[0], "lon": at[1], "moved": f"listed {off:.0f} km from its postcode {e.get('postcode')}; "
                                                             "shown at the postcode"}  # fmt: skip
        if origin is not None:
            e["distance_km"] = round(haversine_km(origin, at), 2)
            if radius_km is not None and e["distance_km"] > radius_km:
                dropped += 1
                continue
        out.append(e)
    return out, moved, dropped


async def check(engine: Any, events: list[dict[str, Any]], origin: tuple[float, float] | None = None,
                radius_km: float | None = None) -> tuple[list[dict[str, Any]], int, int]:  # fmt: skip
    """correct(), with the postcodes looked up first. Unchanged when the table cannot be read."""
    pcs = {pc for e in events if (pc := _compact(e.get("postcode")))}
    points = await postcode_points(engine, pcs)
    if not points:
        return events, 0, 0
    return correct(events, points, origin, radius_km)

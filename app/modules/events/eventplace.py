"""Event listings put in the wrong place: a provider's map position that disagrees with the venue's own postcode.

Ticketmaster in particular sometimes gives a venue the position of somewhere else (a Swansea show placed near home),
so it turns up in a search miles from where it is. Each listing with a postcode is checked against the local
Code-Point Open table: one more than MISPLACED_KM from its postcode is moved to the postcode, its distance worked out
again, and dropped if that puts it outside the search. Without the table (not built yet), or for a postcode it does not
know, a listing is left as it came.

A listing with no postcode (or one the table does not know) but a town is checked against that town's centre: the
middle of the venues already seen in a town of that name, each at its own postcode. One more than TOWN_KM from it is
moved to the centre in the same way. A town no venue with a postcode has been seen in is not checked.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.geoquery import haversine_km

log = logging.getLogger("wa.events")

MISPLACED_KM = 10.0  # a venue's postcode centre is within a few hundred metres of it; 10 km is plainly somewhere else
TOWN_KM = 25.0  # a town's venues spread further than a postcode's: 25 km from its centre is another town
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


def _town(v: Any) -> str | None:
    t = re.sub(r"\s+", " ", str(v or "")).strip().lower()
    return t or None


async def town_points(engine: Any, towns: set[str]) -> dict[str, tuple[float, float]]:
    """Each town's centre: the middle (median) of the venues seen there, each placed at its own postcode."""
    if not towns or engine is None:
        return {}
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT lower(regexp_replace(trim(v.town), '\\s+', ' ', 'g')) AS t, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY p.lat) AS lat, percentile_cont(0.5) WITHIN GROUP (ORDER BY p.lon) AS lon "
                "FROM event_venue v JOIN postcode p ON p.pc = upper(regexp_replace(v.postcode, '\\s', '', 'g')) "
                "WHERE lower(regexp_replace(trim(v.town), '\\s+', ' ', 'g')) = ANY(:t) GROUP BY 1"), {"t": sorted(towns)})).all()  # fmt: skip
    except (DBAPIError, AttributeError, OSError) as exc:
        log.warning("town centres are not available (%s): events without a postcode are not checked", type(exc).__name__)
        return {}
    return {r.t: (float(r.lat), float(r.lon)) for r in rows if r.lat is not None}


def correct(events: list[dict[str, Any]], points: dict[str, tuple[float, float]],
            origin: tuple[float, float] | None = None, radius_km: float | None = None,
            towns: dict[str, tuple[float, float]] | None = None) -> tuple[list[dict[str, Any]], int, int]:  # fmt: skip
    """The events with misplaced ones moved to their postcode (and given a `moved` note), those then outside radius_km
    of origin left out; and how many were moved and dropped."""
    out, moved, dropped = [], 0, 0
    for e in events:
        pc = _compact(e.get("postcode"))
        at = points.get(pc) if pc else None
        limit, by = MISPLACED_KM, f"its postcode {e.get('postcode')}"
        if at is None and towns and (t := _town(e.get("town"))) in towns:
            if e.get("lat") is None or e.get("lon") is None:
                out.append(e)  # no position to doubt, and a town centre is too rough to give one
                continue
            at, limit, by = towns[t], TOWN_KM, f"{e.get('town')}"
        if at is None:
            out.append(e)
            continue
        if e.get("lat") is None or e.get("lon") is None:
            e = {**e, "lat": at[0], "lon": at[1]}
        else:
            off = haversine_km((float(e["lat"]), float(e["lon"])), at)
            if off <= limit:
                out.append(e)
                continue
            moved += 1
            where = "the postcode" if limit == MISPLACED_KM else "the town's centre"
            e = {**e, "lat": at[0], "lon": at[1], "moved": f"listed {off:.0f} km from {by}; shown at {where}"}
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
    if points is None:
        return events, 0, 0
    towns = {t for e in events if (t := _town(e.get("town"))) and _compact(e.get("postcode")) not in points}
    town_at = await town_points(engine, towns)
    if not points and not town_at:
        return events, 0, 0
    return correct(events, points, origin, radius_km, town_at)

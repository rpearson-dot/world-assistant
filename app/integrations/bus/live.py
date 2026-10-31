"""Real positions for the buses a tracker is following, from the Bus Open Data Service (BODS).

TfL's arrival predictions say which buses are heading for your stops, but work out where each one *is* by
inference, and that wobbles. BODS reports each bus's GPS position, bearing and destination, and uses the same
vehicle id (the registration, e.g. LG23FAJ), so the two join directly.

Only gathered when it is needed:

- inside the tracker's own journey window (earliest bus to deadline), not its whole polling window,
- for buses TfL already shows heading for the tracker's stops, not every bus on the line,
- one request a poll, and none at all without a BODS key or with `WA_BUS_LIVE_ENABLED=false`.

A failure here never fails the poll: the tracker works as before without it.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.integrations.bods.client import BodsError, Box, fetch_vehicles
from app.integrations.bus.config import TrackerConfig
from app.integrations.bus.events import to_minutes

log = logging.getLogger("wa.bus.live")

MARGIN_KM = 1.5  # beyond the outermost stop, so a bus approaching the route is caught too
KEEP_DAYS = 30
_FILLER = {"bus", "station", "garage", "stand", "stop", "road", "street", "the", "and"}


def in_journey_window(cfg: TrackerConfig, local_minutes: int) -> bool:
    """Between the earliest bus you would catch and the deadline: the only time live positions are wanted."""
    return to_minutes(cfg.earliest) <= local_minutes <= to_minutes(cfg.deadline)


def known_vehicles(state: dict[str, Any], preds: list[dict[str, Any]], stop_sids: set[str]) -> set[str]:
    """Buses TfL shows heading for this tracker's stops, plus any already followed past one of them. The direction
    label is not used: it is not reliable (see test_the_direction_label_does_not_decide_whether_a_bus_counts)."""
    out = {p["vehicleId"] for p in preds if p.get("vehicleId") and p.get("naptanId") in stop_sids}
    for vehicle, trip in (state.get("trips") or {}).items():
        if any(s.get("sid") in stop_sids for s in trip.get("stops") or []):
            out.add(vehicle)
    return out


def _words(s: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if len(w) > 2 and w not in _FILLER}


def direction_of(destination: str | None, towards: dict[str, str]) -> str | None:
    """Which of our directions a bus is heading, from its destination board, by words shared with where each
    direction ends ("Fairfield Bus Station" and "Kingston Fairfield Bus Station"). None if it is not clear."""
    scores = {d: len(_words(destination) & _words(name)) for d, name in towards.items()}
    best = max(scores.values(), default=0)
    winners = [d for d, s in scores.items() if s == best]
    return winners[0] if best > 0 and len(winners) == 1 else None


def note(v: dict[str, Any], towards: dict[str, str]) -> dict[str, Any]:
    d = direction_of(v.get("destination"), towards)
    return {"destination": v.get("destination"), "bearing": v.get("bearing"), "lat": v.get("lat"),
            "lon": v.get("lon"), "recorded_at": v.get("recorded_at"), "direction": d,
            "towards": towards.get(d) if d else None}  # fmt: skip


def attach(anomalies: list[dict[str, Any]], by_vehicle: dict[str, dict[str, Any]], towards: dict[str, str]) -> int:
    """Add what the GPS says to each event about a bus it can see. Returns how many were annotated."""
    done = 0
    for a in anomalies:
        v = by_vehicle.get(a.get("vehicle", ""))
        if not v:
            continue
        a["live"] = note(v, towards)
        done += 1
    return done


async def route_box(conn: AsyncConnection, sids: list[str]) -> Box | None:
    """A box around the tracker's stops, from the national stop register. None if it knows none of them."""
    row = (await conn.execute(
        text("SELECT min(lat), max(lat), min(lon), max(lon), count(*) FROM naptan_stop WHERE atco = ANY(:s)"),
        {"s": sids})).first()  # fmt: skip
    if row is None or not row[4]:
        return None
    lat_min, lat_max, lon_min, lon_max = row[0], row[1], row[2], row[3]
    dlat = MARGIN_KM / 111.0
    dlon = MARGIN_KM / (111.0 * max(math.cos(math.radians((lat_min + lat_max) / 2)), 0.1))
    return Box(lon_min - dlon, lat_min - dlat, lon_max + dlon, lat_max + dlat)


async def gather(conn: AsyncConnection, api_key: str, cfg: TrackerConfig, sids: list[str],
                 vehicles: set[str]) -> list[dict[str, Any]]:  # fmt: skip
    """The followed buses' current GPS positions on this line. Empty (and logged) if it cannot be done."""
    if not vehicles or not api_key:
        return []
    box = await route_box(conn, sids)
    if box is None:
        log.info("live positions: the stop register has none of this route's stops, so no box to ask for")
        return []
    try:
        found = await fetch_vehicles(api_key, box)
    except BodsError as exc:
        log.warning("live positions skipped: %s", exc)
        return []
    line = cfg.line.upper()
    return [v for v in found if v.get("vehicle") in vehicles and (v.get("line") or "").upper() == line]

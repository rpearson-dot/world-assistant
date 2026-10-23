"""A bus line followed with the Bus Open Data Service instead of TfL, for any route in Britain.

The tracker follows each bus from "which stop is it heading for, and how soon". TfL says that directly (its
Arrivals feed). BODS does not: it gives each bus's GPS position and the timetable gives the stops in order.
So `arrivals` places each bus along its route from its position and turns that into predictions of the same
shape TfL's feed has, and everything after that (trips, legs, hits, reliability) is the same code.

The estimate is distance along the route at a typical bus speed, so it does not see traffic. It is a looser
countdown than TfL's, and the page says where a line is on it.

Not run against the live service from where this was written; tested on positions and timetables built to the
shape BODS documents.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from app.integrations.bods.client import (
    MAX_DATASETS,
    BodsError,
    Box,
    download_txc,
    find_datasets,
    operators_of_line,
)
from app.integrations.bods.txc import Route, TxcError, parse_txc
from app.integrations.bus.live import direction_of

SPEED_MS = 4.5  # about 16 km/h door to door: a London bus with its stops
MAX_OFF_ROUTE_M = 250.0  # a bus further than this from the route is not on it (a depot, a diversion)
HORIZON_S = 30 * 60  # predictions further away than this are not made, as TfL's feed does not
STALE_S = 180  # a position older than this is not where the bus is now
BEARING_TOLERANCE = 100.0  # degrees: a bus pointing further from the route's way than this is going the other way


@dataclass(frozen=True)
class Stop:
    sid: str
    name: str
    lat: float
    lon: float


def _metres(lat0: float, a: tuple[float, float]) -> tuple[float, float]:
    """(lat, lon) to local metres east and north of a reference latitude."""
    return (a[1] * 111_320.0 * math.cos(math.radians(lat0)), a[0] * 110_540.0)


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) + 360.0) % 360.0


def _angle_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def place_on_route(stops: list[Stop], lat: float, lon: float, bearing: float | None) -> tuple[float, float, float] | None:
    """(metres along the route, distance from it, score) at the closest point of the stops joined up. A segment the bus
    is pointing away from counts as 1 km further, so a bus on the other carriageway is placed on the other way."""
    if len(stops) < 2:
        return None
    lat0 = stops[0].lat
    p = _metres(lat0, (lat, lon))
    pts = [_metres(lat0, (s.lat, s.lon)) for s in stops]
    best: tuple[float, float, float] | None = None  # (score, along, distance)
    along = 0.0
    for a, b in pairwise(pts):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg = math.hypot(dx, dy)
        if seg > 0:
            t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (seg * seg)))
            dist = math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))
            heading = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
            score = dist + (0.0 if bearing is None or _angle_gap(bearing, heading) <= BEARING_TOLERANCE else 1000.0)
            if best is None or score < best[0]:
                best = (score, along + t * seg, dist)
        along += seg
    return (best[1], best[2], best[0]) if best else None


def stop_distances(stops: list[Stop]) -> list[float]:
    """Metres along the route to each stop."""
    lat0 = stops[0].lat if stops else 0.0
    pts = [_metres(lat0, (s.lat, s.lon)) for s in stops]
    out, total = [0.0], 0.0
    for a, b in pairwise(pts):
        total += math.hypot(b[0] - a[0], b[1] - a[1])
        out.append(total)
    return out[: len(stops)]


def arrivals(
    vehicles: list[dict[str, Any]], routes: dict[str, list[Stop]], line: str, now: float,
    speed: float = SPEED_MS,
) -> list[dict[str, Any]]:  # fmt: skip
    """Predictions for every stop ahead of every bus on the line, in the shape of TfL's Arrivals rows
    (vehicleId, naptanId, timeToStation, expectedArrival, destinationName). Buses not on the route are dropped."""
    towards = {d: s[-1].name for d, s in routes.items() if s}
    cum = {d: stop_distances(s) for d, s in routes.items()}
    out: list[dict[str, Any]] = []
    for v in vehicles:
        if (v.get("line") or "").upper() != line.upper() or not v.get("vehicle"):
            continue
        if v.get("recorded_at"):
            try:
                age = now - datetime.fromisoformat(v["recorded_at"]).timestamp()
            except ValueError:
                age = 0.0
            if age > STALE_S:
                continue
        wanted = direction_of(v.get("destination"), towards)
        best: tuple[float, str, float] | None = None  # (score, direction, metres along)
        for d, stops in routes.items():
            if wanted and d != wanted:
                continue
            placed = place_on_route(stops, v["lat"], v["lon"], v.get("bearing"))
            if placed is None or placed[1] > MAX_OFF_ROUTE_M:
                continue
            if best is None or placed[2] < best[0]:
                best = (placed[2], d, placed[0])  # the way it is pointing wins when the destination did not say
        if best is None:
            continue
        _, d, along = best
        for stop, at in zip(routes[d], cum[d], strict=True):
            if at <= along + 15.0:  # already passed, or at it
                continue
            eta = (at - along) / speed
            if eta > HORIZON_S:
                break
            out.append({
                "vehicleId": v["vehicle"], "naptanId": stop.sid, "stationName": stop.name, "lineId": line,
                "direction": d, "destinationName": v.get("destination"), "timeToStation": round(eta),
                "expectedArrival": (datetime.fromtimestamp(now, UTC) + timedelta(seconds=eta)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })  # fmt: skip
    return out


RouteCheck = Callable[[Route], Awaitable[bool]]


def near_check(coords_of: Callable[[list[str]], Awaitable[dict[str, tuple[float, float]]]], box: Box, margin_km: float = 10.0) -> RouteCheck:
    """A check that a timetable is for the route the buses are on: most of its stops that NaPTAN can place must
    lie in `box` (grown by `margin_km`). Line numbers repeat across Britain, so a name match alone proves nothing.
    With no stop placed at all there is nothing to judge by, so it passes."""
    pad_lat = margin_km / 111.0
    pad_lon = margin_km / (111.0 * max(math.cos(math.radians((box.min_lat + box.max_lat) / 2)), 0.1))

    async def check(route: Route) -> bool:
        sids = sorted({x["id"] for stops in route.sequences().values() for x in stops})
        placed = await coords_of(sids)
        if not placed:
            return True
        inside = sum(1 for lat, lon in placed.values()
                     if box.min_lat - pad_lat <= lat <= box.max_lat + pad_lat
                     and box.min_lon - pad_lon <= lon <= box.max_lon + pad_lon)  # fmt: skip
        return inside * 2 >= len(placed)

    return check


async def load_route(
    api_key: str, line: str, noc: str | None = None, box: Box | None = None, check: RouteCheck | None = None
) -> tuple[Route, str | None]:
    """Find the line's timetable on BODS through its operator (given, or read from the buses near `box`).

    There is deliberately no search by line number alone: the same number is a different route in every town, and
    the first match is usually the wrong one. `check` (see `near_check`) rejects a timetable whose stops are
    not where the buses are. Returns the route and the operator code that had it."""
    nocs = [noc] if noc else (await operators_of_line(api_key, line, box) if box else [])
    if not nocs:
        raise BodsError(f"BODS has no operator for line {line!r}" + (" near there: no bus is running it now" if box else ": give its operator or a place"))
    tried: list[tuple[str, dict[str, Any]]] = []
    for n in nocs:
        tried += [(n, d) for d in await find_datasets(api_key, noc=n)]
    if not tried:
        raise BodsError(f"BODS has no timetable files for {', '.join(nocs)} (some operators, such as TfL's bus companies, do not publish them)")
    notes: list[str] = []
    for n, ds in tried[:MAX_DATASETS]:
        name = str(ds.get("name") or ds.get("id") or "a file")[:40]
        try:
            bodies = await download_txc(ds["url"], line=line)
        except BodsError as exc:
            notes.append(f"{name}: {exc}")
            continue
        found = rejected = 0
        for body in bodies:
            try:
                route = parse_txc(body, line)
            except TxcError as exc:
                notes.append(f"{name}: {exc}")
                continue
            if route is None:
                continue
            found += 1
            if check is None or await check(route):
                return route, n
            rejected += 1
        notes.append(f"{name}: {len(bodies)} files name the line, {found} had it" + (", all rejected as not near the buses" if rejected else ""))
    why = "; ".join(notes[:5]) + (f"; and {len(notes) - 5} more" if len(notes) > 5 else "")
    raise BodsError(f"BODS has no timetable for line {line!r}" + (" near there" if box else "") + (f" ({why})" if why else ""))


class BodsLineClient:
    """The two calls the tracker makes of a timetable source (the stop order, the times at a stop), from BODS."""

    def __init__(self, api_key: str, noc: str | None = None, box: Box | None = None, check: RouteCheck | None = None) -> None:
        self._key, self.noc, self._box, self._check = api_key, noc, box, check
        self._route: dict[str, Route] = {}

    async def route(self, line: str) -> Route:
        if line not in self._route:
            self._route[line], found = await load_route(self._key, line, self.noc, self._box, self._check)
            self.noc = self.noc or found
        return self._route[line]

    async def route_sequence(self, line: str, direction: str) -> list[dict[str, str]]:
        return (await self.route(line)).sequences().get(direction, [])

    async def timetable_times(self, line: str, stop: str, direction: str) -> list[str]:
        return (await self.route(line)).times_at(stop, direction)

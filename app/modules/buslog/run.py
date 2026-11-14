"""The school run: every bus that leaves the first stop (Worcester Park) towards Kingston in the morning, and what
happened to it on the way to the school's stop (Coombe Girls School). Pure: given the route, the day's timetable, each
bus's GPS positions (BODS) and TfL's predictions, so it is tested on its own.

How a bus is followed
- Each GPS position is placed on the route's line towards Kingston: how far along it is (metres from the start) and
  how far from the line it is. Positions come about once a minute.
- **Going forwards**, a stop is passed between two positions either side of it; the time in between (by distance) is
  the *probable* time it was there.
- **Turning round**: once a bus is 400 m or more behind the furthest point it reached while still on the route, it
  has turned; the place is the nearest stop to that furthest point (Blakes Lane towards Kingston and then towards
  Sutton: it turned at the A3 roundabout, St James Church / Kingston By-Pass).
- **Off the route**: positions more than 150 m from the line. Where it rejoins says what it missed: further along,
  the stops in between were skipped; behind, it turned.
- **The school's other stop**: a bus at the school going the other way (or that TfL says reached the stop on the
  return side) reached the school from the other side. It counts as having got there.

TfL's predictions give the start-of-day expectation (the first time TfL predicted the bus at each stop), the revised
times (the last before it got there), when a delay was first shown (5 minutes or more against the timetable), and
the *actual* time at a stop: TfL stops predicting a bus at a stop when it gets there, so a prediction that ends
within a couple of minutes of when it was expected is an arrival. One that ends while the GPS shows the bus
somewhere else (turned, or off the route) is "dropped", not an arrival.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

ON_ROUTE_M = 80  # within this of the line: on the route
OFF_ROUTE_M = 150  # further than this: off it
TURNED_M = 400  # this far behind the furthest point reached: turned round
MAX_GAP_S = 300  # two positions further apart than this say nothing about the stops between them
MAX_STEP_M = 3500  # nor two positions this far apart along the line (a jump, not a drive)
ARRIVED_S = 150  # a prediction that ends within this of its time is an arrival
LATE_S = 300  # a delay worth saying: five minutes against the timetable
MATCH_S = 20 * 60  # a bus is running a timetabled journey whose time at the first stop is within this
PLACES = {"kingston by-pass": "the A3 roundabout", "fountain roundabout": "the Fountain roundabout"}


def _xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    return (lon - lon0) * 111_320 * math.cos(math.radians(lat0)), (lat - lat0) * 110_540


class Path:
    """A line (lon, lat points) with distance along it, to place a position: (metres along, metres from it)."""

    def __init__(self, coords: list[list[float]]) -> None:
        pts = [(float(p[1]), float(p[0])) for p in coords]
        self.lat0, self.lon0 = pts[0]
        self.xy = [_xy(a, b, self.lat0, self.lon0) for a, b in pts]
        self.at = [0.0]
        for (x1, y1), (x2, y2) in zip(self.xy, self.xy[1:], strict=False):
            self.at.append(self.at[-1] + math.hypot(x2 - x1, y2 - y1))
        self.length = self.at[-1]

    def locate(self, lat: float, lon: float, near: float | None = None, within: float = 2500) -> tuple[float, float]:
        """(metres along, metres off) of the closest point on the line; near: only consider the line within `within`
        metres of that measure (so a road the route uses twice is not confused)."""
        px, py = _xy(lat, lon, self.lat0, self.lon0)
        best = (0.0, float("inf"))
        for i in range(len(self.xy) - 1):
            if near is not None and (self.at[i + 1] < near - within or self.at[i] > near + within):
                continue
            (x1, y1), (x2, y2) = self.xy[i], self.xy[i + 1]
            dx, dy = x2 - x1, y2 - y1
            seg = dx * dx + dy * dy
            t = 0.0 if seg == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg))
            d = math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
            if d < best[1]:
                best = (self.at[i] + t * math.sqrt(seg), d)
        return best


@dataclass
class Fix:
    at: datetime
    lat: float
    lon: float
    m: float = 0.0  # metres along the line
    off: float = 0.0  # metres from it
    origin_departure: datetime | None = None
    destination: str | None = None
    capacity: dict[str, Any] | None = None  # how full, when BODS says (capacity.read)


@dataclass
class Trip:
    vehicle: str
    fixes: list[Fix]
    left_at: datetime | None  # probable time at the first stop (GPS)
    events: list[dict[str, Any]] = field(default_factory=list)


def stops_along(path: Path, stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The route's stops with how far along the line each is (in order: each placed after the one before)."""
    out, last = [], 0.0
    for s in stops:
        if s.get("lat") is None:
            continue
        m, off = path.locate(s["lat"], s["lon"], near=last + 1500, within=3000) if out else path.locate(s["lat"], s["lon"])
        out.append({**s, "m": m, "off": off})
        last = m
    return out


def place(path: Path, fixes: list[Fix]) -> list[Fix]:
    """Each position placed on the line, following on from the one before where it is close enough."""
    prev: Fix | None = None
    for f in fixes:
        if prev is not None and prev.off <= OFF_ROUTE_M and (f.at - prev.at).total_seconds() <= MAX_GAP_S:
            m, off = path.locate(f.lat, f.lon, near=prev.m, within=MAX_STEP_M)
            if off > OFF_ROUTE_M:  # perhaps it is somewhere else on the line altogether
                m2, off2 = path.locate(f.lat, f.lon)
                if off2 < off:
                    m, off = m2, off2
        else:
            m, off = path.locate(f.lat, f.lon)
        f.m, f.off = m, off
        prev = f
    return fixes


def _cross(a: Fix, b: Fix, m: float) -> datetime | None:
    """When a bus going from a to b passed measure m (either way), if it did and the two fixes can say."""
    if a.off > ON_ROUTE_M or b.off > ON_ROUTE_M or (b.at - a.at).total_seconds() > MAX_GAP_S or abs(b.m - a.m) > MAX_STEP_M:
        return None
    lo, hi = sorted((a.m, b.m))
    if not (lo < m <= hi) or a.m == b.m:
        return None
    return a.at + (b.at - a.at) * ((m - a.m) / (b.m - a.m))


def trips(vehicle: str, fixes: list[Fix], start_m: float) -> list[Trip]:
    """A bus's journeys past the first stop going forwards (a bus can make more than one in a morning)."""
    starts: list[tuple[int, datetime]] = []
    for i in range(len(fixes) - 1):
        a, b = fixes[i], fixes[i + 1]
        t = _cross(a, b, start_m)
        if t and b.m > a.m:
            starts.append((i, t))
    if not starts and fixes:  # first seen just past the stop, heading on: it left before the first fix
        head = [f for f in fixes[:3] if f.off <= ON_ROUTE_M]
        if len(head) >= 2 and start_m < head[0].m <= start_m + 1200 and head[-1].m > head[0].m:
            starts.append((0, None))  # type: ignore[arg-type]
    out = []
    for k, (i, t) in enumerate(starts):
        end = starts[k + 1][0] + 1 if k + 1 < len(starts) else len(fixes)
        out.append(Trip(vehicle, fixes[i:end], t))
    return out


def follow(trip: Trip, stops: list[dict[str, Any]], start: int, school: int, path_end: float) -> dict[str, Any]:
    """What a trip did from the first stop: the stops it passed (probable times), where it turned or left the route,
    and what it missed. start, school: indexes in stops."""
    fx = trip.fixes
    passed: dict[str, datetime] = {}
    backwards: dict[str, datetime] = {}
    if trip.left_at:
        passed[stops[start]["stop"]] = trip.left_at
    furthest, furthest_fix = -1.0, None
    turned: dict[str, Any] | None = None
    off_runs: list[dict[str, Any]] = []
    run: dict[str, Any] | None = None
    last_on: Fix | None = None
    for i, f in enumerate(fx):
        at_end = f.m >= path_end - 150 and f.off <= OFF_ROUTE_M
        if last_on is not None and last_on.m >= path_end - 150:
            fx = fx[:i]  # the end of the route: what it does next is another journey
            break
        if f.off <= ON_ROUTE_M or at_end:
            if run is not None:  # back on the route
                run["rejoined_at"], run["rejoined_m"] = f.at, f.m
                nxt = next((g for g in fx[i + 1:i + 3] if g.off <= ON_ROUTE_M), None)
                run["heading_back"] = bool(nxt and nxt.m < f.m - 50)
                off_runs.append(run)
                run = None
            if turned is None and f.m > furthest:
                furthest, furthest_fix = f.m, f
            if turned is None and furthest_fix is not None and furthest - f.m >= TURNED_M and not at_end:
                turned = {"at": furthest_fix.at, "m": furthest, "seen_back_at": f.at}
            last_on = f
        elif f.off > OFF_ROUTE_M and turned is None:
            if run is None:
                run = {"left_at": f.at, "left_m": last_on.m if last_on else f.m, "last_on_at": last_on.at if last_on else None,
                       "furthest_off_m": f.off}  # fmt: skip
            run["furthest_off_m"] = max(run["furthest_off_m"], f.off)
        if i + 1 < len(fx):
            b = fx[i + 1]
            for s in stops[start + 1:]:
                t = _cross(f, b, s["m"])
                if t is None:
                    continue
                if b.m > f.m and turned is None and s["stop"] not in passed:
                    passed[s["stop"]] = t
                elif b.m < f.m and s["stop"] not in backwards:
                    backwards[s["stop"]] = t
    if run is not None:
        off_runs.append({**run, "rejoined_at": None, "rejoined_m": None, "heading_back": False})
    # a run off the route that came back behind where it left, or came back heading towards Sutton, is a turn made
    # off the route
    for r in off_runs:
        back = r["rejoined_m"] is not None and (r["rejoined_m"] < r["left_m"] - TURNED_M or r["heading_back"])
        if back and (turned is None or r["left_at"] <= turned["at"]):
            turned = {"at": r["left_at"], "m": r["left_m"], "seen_back_at": r["rejoined_at"], "off_route": True,
                      "back_at_m": r["rejoined_m"]}  # fmt: skip
        r["turned"] = back
    skipped: list[str] = []
    for r in off_runs:
        if r["rejoined_m"] is not None and r["rejoined_m"] > r["left_m"] + 100:
            r["missed"] = [s["stop"] for s in stops[start + 1:] if r["left_m"] < s["m"] < r["rejoined_m"]
                           and s["stop"] not in passed and s["stop"] not in backwards]  # fmt: skip
            if not r["turned"]:
                skipped += r["missed"]
        else:
            r["missed"] = []
    return {"passed": passed, "backwards": backwards, "turned": turned, "off_route": off_runs, "skipped": skipped,
            "furthest_m": furthest, "last_fix": fx[-1].at if fx else None, "finished": bool(last_on and last_on.m >= path_end - 150)}  # fmt: skip


def place_name(stops: list[dict[str, Any]], m: float, before: bool = False) -> dict[str, Any]:
    """The stop nearest a point on the line (before: the last stop at or before it), and what the place is known as
    (the A3 roundabout)."""
    s = min(stops, key=lambda s: abs(s["m"] - m))
    if before:
        s = max((x for x in stops if x["m"] <= m + 30), key=lambda x: x["m"], default=s)
    name = s.get("name") or s["stop"]
    known = next((v for k, v in PLACES.items() if k in name.lower()), None)
    return {"stop": s["stop"], "name": name, "place": known, "words": f"{known} ({name})" if known else name}


# --- TfL's predictions for one bus at one stop ---------------------------------------------------------------------


def tfl_at_stop(rows: list[dict[str, Any]], scheduled: datetime | None) -> dict[str, Any]:
    """From TfL's prediction changes for one bus at one stop (in time order): the first expected time (and when it
    was first given), the last before it was dropped, whether dropping it was an arrival, and when TfL first showed
    it 5 minutes or more late."""
    live = [r for r in rows if r["change"] != "gone" and r.get("expected_at")]
    if not live:
        return {}
    first, last = live[0], live[-1]
    gone = next((r for r in rows if r["change"] == "gone" and r["seen_at"] >= last["seen_at"]), None)
    arrived = None
    if gone is not None and abs((gone["seen_at"] - last["expected_at"]).total_seconds()) <= ARRIVED_S:
        arrived = min(gone["seen_at"], max(last["expected_at"], last["seen_at"]))
    late = None
    if scheduled is not None:
        for r in live:
            d = (r["expected_at"] - scheduled).total_seconds()
            if d >= LATE_S:
                late = {"seen_at": r["seen_at"], "late_s": round(d), "expected_at": r["expected_at"]}
                break
    return {"first_expected": first["expected_at"], "first_seen": first["seen_at"], "last_expected": last["expected_at"],
            "last_seen": last["seen_at"], "dropped_at": gone["seen_at"] if gone else None, "arrived": arrived,
            "late": late}  # fmt: skip


# --- the morning ----------------------------------------------------------------------------------------------------


def _journey_for(trips_: list[dict[str, Any]], journeys: dict[int, dict[str, datetime]], first: str, origin: str | None) -> None:
    """Which timetabled journey each trip is: the one whose start from the route's origin matches the bus's own
    (BODS says), else the closest at the first stop, each journey once (closest first)."""
    used: set[int] = set()
    for t in trips_:
        o = t.get("origin_departure")
        if o is None or origin is None:
            continue
        j = min(((abs((times[origin] - o).total_seconds()), j) for j, times in journeys.items() if origin in times and j not in used),
                default=(None, None))  # fmt: skip
        if j[0] is not None and j[0] <= 90:
            t["journey"] = j[1]
            used.add(j[1])
    pairs = []
    for k, t in enumerate(trips_):
        if t.get("journey") is not None or t.get("first_at") is None:
            continue
        for j, times in journeys.items():
            if first in times and j not in used:
                d = abs((times[first] - t["first_at"]).total_seconds())
                if d <= MATCH_S:
                    pairs.append((d, k, j))
    for _, k, j in sorted(pairs):
        if trips_[k].get("journey") is None and j not in used:
            trips_[k]["journey"] = j
            used.add(j)


def morning(route: list[dict[str, Any]], shape: list[list[float]] | None, other: list[dict[str, Any]],
            timetable: list[dict[str, Any]], positions: dict[str, list[dict[str, Any]]],
            predictions: list[dict[str, Any]], first: str, school: str, school_other: str | None,
            window: tuple[datetime, datetime], by: datetime | None = None,
            gaps: list[tuple[datetime, datetime]] | None = None) -> dict[str, Any]:
    """The morning's school run.

    route: the stops towards Kingston in order [{stop, name, lat, lon}]; shape: its line (lon, lat points; the
    stops are used when there is none); other: the stops the other way (for the school's other stop's name);
    timetable: [{direction?, journey, stop, scheduled_at}] towards Kingston; positions: vehicle -> [{recorded_at,
    lat, lon, origin_aimed_departure, destination_name}]; predictions: [{seen_at, vehicle, stop, change,
    expected_at}] (every direction); first, school, school_other: stop ids; window: the recording window (journeys
    timetabled at the first stop inside it are the morning's); by: when it must be at the school; gaps: spans
    with no positions answered (a bus not seen then is "no data", not "did not run")."""
    path = Path(shape if shape and len(shape) >= 2 else [[s["lon"], s["lat"]] for s in route if s.get("lat") is not None])
    stops = stops_along(path, route)
    idx = {s["stop"]: i for i, s in enumerate(stops)}
    if first not in idx or school not in idx:
        raise ValueError("the first stop and the school's stop must both be on the route towards Kingston")
    a, b = idx[first], idx[school]
    names = {s["stop"]: s.get("name") or s["stop"] for s in [*route, *other]}
    origin = stops[0]["stop"]
    journeys: dict[int, dict[str, datetime]] = {}
    for r in timetable:
        journeys.setdefault(r["journey"], {})[r["stop"]] = r["scheduled_at"]
    morning_j = {j: t for j, t in journeys.items() if first in t and window[0] <= t[first] < window[1]}
    by_bus: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(predictions, key=lambda p: p["seen_at"]):
        by_bus.setdefault(_norm(p["vehicle"]), []).append(p)

    found: list[dict[str, Any]] = []
    for vehicle, rows in positions.items():
        fx = sorted({r["recorded_at"]: Fix(r["recorded_at"], r["lat"], r["lon"], origin_departure=r.get("origin_aimed_departure"),
                                           destination=r.get("destination_name"), capacity=r.get("capacity"))
                     for r in rows if r.get("lat") is not None and r.get("recorded_at")}.values(), key=lambda f: f.at)  # fmt: skip
        place(path, fx)
        for tr in trips(vehicle, fx, stops[a]["m"]):
            od = next((f.origin_departure for f in tr.fixes if f.origin_departure), None)
            found.append({"vehicle": vehicle, "trip": tr, "first_at": tr.left_at or tr.fixes[0].at, "origin_departure": od,
                          "destination": next((f.destination for f in tr.fixes if f.destination), None)})  # fmt: skip
    _journey_for(found, journeys, first, origin)

    buses = []
    for t in found:
        j = t.get("journey")
        sched = journeys.get(j, {}) if j is not None else {}
        if j is not None and j not in morning_j:
            continue  # a journey outside the morning's
        if j is None and not (window[0] <= t["first_at"] < window[1] + timedelta(minutes=15)):
            continue
        buses.append(_bus(t, stops, a, b, sched, by_bus.get(_norm(t["vehicle"]), []), school_other, names, path.length, by))
    seen = {x["journey"] for x in buses if x["journey"] is not None}
    for j, times in morning_j.items():
        if j in seen:
            continue
        due = times[first]
        nodata = any(g0 - timedelta(minutes=5) <= due <= g1 + timedelta(minutes=5) for g0, g1 in gaps or [])
        buses.append({"vehicle": None, "journey": j, "due_first": due, "due_school": times.get(school), "left_first": None,
                      "outcome": "no_data" if nodata else "not_seen", "made_it": False, "in_time": False,
                      "words": "no positions came in around then: not known" if nodata else
                               "no bus was seen running it (positions were coming in)", "stops": [], "events": []})  # fmt: skip
    buses.sort(key=lambda x: x.get("due_first") or x.get("left_first") or window[1])
    recorded = [x for x in buses if x["vehicle"]]
    made = [x for x in recorded if x["made_it"]]
    summary = {
        "timetabled": len(morning_j), "recorded": len(recorded), "made_it": len(made),
        "other_side": sum(1 for x in made if x["outcome"] == "other_side"),
        "in_time": sum(1 for x in made if x["in_time"]),
        "turned": sum(1 for x in recorded if x["outcome"] == "turned"),
        "skipped": sum(1 for x in recorded if x["outcome"] in ("skipped", "left_route")),
        "lost": sum(1 for x in recorded if x["outcome"] == "lost"),
        "not_seen": sum(1 for x in buses if x["outcome"] == "not_seen"),
        "no_data": sum(1 for x in buses if x["outcome"] == "no_data"),
        "percent": round(100 * len(made) / len(recorded)) if recorded else None,
    }
    events = sorted((e for x in buses for e in x["events"]), key=lambda e: e["at"])
    return {"first": {"stop": first, "name": names.get(first)}, "school": {"stop": school, "name": names.get(school)},
            "school_other": {"stop": school_other, "name": names.get(school_other or "")} if school_other else None,
            "by": by, "summary": summary, "buses": buses, "events": events}  # fmt: skip


def _capacity_at(fixes: list[Fix], at: datetime | None) -> dict[str, Any] | None:
    """How full the bus was when it was at a stop: from the position nearest that time (within three minutes)."""
    if at is None:
        return None
    near = [f for f in fixes if f.capacity and abs((f.at - at).total_seconds()) <= 180]
    return min(near, key=lambda f: abs((f.at - at).total_seconds())).capacity if near else None


def _norm(v: str) -> str:
    return "".join(ch for ch in v.upper() if ch.isalnum())


def _bus(t: dict[str, Any], stops: list[dict[str, Any]], a: int, b: int, sched: dict[str, datetime],
         preds: list[dict[str, Any]], school_other: str | None, names: dict[str, str], length: float,
         by: datetime | None) -> dict[str, Any]:
    tr: Trip = t["trip"]
    seen = follow(tr, stops, a, b, length)
    v = t["vehicle"]
    t0 = (tr.left_at or tr.fixes[0].at) - timedelta(minutes=45)
    end = seen["last_fix"] or tr.fixes[-1].at
    t1 = end + timedelta(minutes=10)
    # this journey's predictions: given before it ended, for times around it (not the bus's next journey)
    mine = [p for p in preds if t0 <= p["seen_at"] <= t1 and (p.get("expected_at") is None or p["expected_at"] <= t1)]
    at_stop: dict[str, list[dict[str, Any]]] = {}
    for p in mine:
        at_stop.setdefault(p["stop"], []).append(p)
    school = stops[b]["stop"]
    turned = seen["turned"]
    turn_place = place_name(stops, turned["m"], before=True) if turned else None

    rows = []
    for s in stops[a:]:
        sid = s["stop"]
        tfl = tfl_at_stop(at_stop.get(sid, []), sched.get(sid))
        gps = seen["passed"].get(sid)
        note, actual = "", tfl.get("arrived")
        if turned and s["m"] > turned["m"] + 30:
            note = "not reached: turned at " + turn_place["words"]  # type: ignore[index]
            if actual:
                note += " (TfL dropped it)"
            actual = None
        elif sid in seen["skipped"]:
            note = "missed: off the route"
            actual = None
        elif gps is None and actual is None and s["m"] > seen["furthest_m"] + 30:
            note = "not known: no position this far"

        elif sid in seen["backwards"] and gps is None:
            note = "passed going the other way"
        rows.append({"stop": sid, "name": s.get("name"), "scheduled": sched.get(sid), "first_expected": tfl.get("first_expected"),
                     "first_seen": tfl.get("first_seen"), "last_expected": tfl.get("last_expected"), "actual": actual,
                     "probable": gps, "late": tfl.get("late"), "note": note,
                     "capacity": _capacity_at(tr.fixes, actual or gps)})  # fmt: skip
    reached_school = school in seen["passed"] or next((r["actual"] for r in rows if r["stop"] == school), None) is not None
    other_side = None
    if not reached_school and school_other:
        if school in seen["backwards"]:
            other_side = seen["backwards"][school]
        else:
            o = tfl_at_stop([p for p in mine if p["stop"] == school_other], None)
            if o.get("arrived"):
                other_side = o["arrived"]

    school_row = next(r for r in rows if r["stop"] == school)
    arrived_school = school_row["actual"] or school_row["probable"] or other_side
    events: list[dict[str, Any]] = []
    if tr.left_at:
        events.append({"at": tr.left_at, "vehicle": v, "kind": "left", "text": f"{v} left {names.get(stops[a]['stop'])}"})
    if reached_school or other_side:
        outcome = "made_it" if reached_school else "other_side"
        words = "reached the school" if reached_school else "reached the school from the other side of the road"
        how = next((r for r in seen["off_route"] if r["turned"]), None)
        if not reached_school and how:
            words += (f": left the route after {place_name(stops, how['left_m'])['words']} and came back on it at "
                      f"{place_name(stops, how['rejoined_m'])['words']} heading back towards Sutton")  # fmt: skip
    elif turned:
        outcome = "turned"
        words = "turned at " + turn_place["words"]  # type: ignore[index]
        if turned.get("off_route"):
            words = (f"left the route after {turn_place['words']} and came back on it at "  # type: ignore[index]
                     f"{place_name(stops, turned['back_at_m'])['words']} heading back towards Sutton")
    elif any(r["rejoined_m"] is not None and r["rejoined_m"] > stops[b]["m"] for r in seen["off_route"]):
        outcome = "skipped"
        r = next(r for r in seen["off_route"] if r["rejoined_m"] is not None and r["rejoined_m"] > stops[b]["m"])
        words = (f"left the route after {place_name(stops, r['left_m'])['words']} and rejoined at "
                 f"{place_name(stops, r['rejoined_m'])['words']}: missed the school")
    elif any(r["rejoined_m"] is None for r in seen["off_route"]):
        outcome = "left_route"
        r = next(r for r in seen["off_route"] if r["rejoined_m"] is None)
        words = f"left the route after {place_name(stops, r['left_m'])['words']} and was not seen back on it"
    else:
        outcome = "lost"
        last = place_name(stops, seen["furthest_m"]) if seen["furthest_m"] >= 0 else None
        words = "positions stopped" + (f" near {last['words']}" if last else "") + " before the school: not known"
    if turned and not turned.get("off_route"):
        events.append({"at": turned["at"], "vehicle": v, "kind": "turned", "text": f"{v} turned at {turn_place['words']}"})  # type: ignore[index]
    for r in seen["off_route"]:
        events.append({"at": r["left_at"], "vehicle": v, "kind": "off_route",
                       "text": f"{v} left the route after {place_name(stops, r['left_m'])['words']}"})  # fmt: skip
        if r["rejoined_at"] and r["turned"]:
            events.append({"at": r["rejoined_at"], "vehicle": v, "kind": "rejoined",
                           "text": f"{v} back on the route at {place_name(stops, r['rejoined_m'])['words']}, heading back"})  # fmt: skip
        elif r["rejoined_at"] and r["missed"]:
            events.append({"at": r["rejoined_at"], "vehicle": v, "kind": "rejoined",
                           "text": f"{v} back on the route at {place_name(stops, r['rejoined_m'])['words']}, "
                                   f"missing {len(r['missed'])} stop{'s' if len(r['missed']) != 1 else ''}"})  # fmt: skip
    if arrived_school:
        events.append({"at": arrived_school, "vehicle": v, "kind": "school",
                       "text": f"{v} at the school" + (" (other side)" if outcome == "other_side" else "")})  # fmt: skip
    late = next((r["late"] for r in rows if r["late"]), None)
    if late:
        events.append({"at": late["seen_at"], "vehicle": v, "kind": "delay",
                       "text": f"TfL shows {v} {round(late['late_s'] / 60)} min late"})  # fmt: skip
    first_row = rows[0]
    return {"vehicle": v, "journey": t.get("journey"), "destination": t.get("destination"),
            "due_first": sched.get(stops[a]["stop"]), "left_first": first_row["actual"] or first_row["probable"],
            "left_first_actual": first_row["actual"] is not None,
            "due_school": sched.get(school), "expected_school": school_row["last_expected"],
            "first_expected_school": school_row["first_expected"], "arrived_school": arrived_school,
            "arrived_school_actual": school_row["actual"] is not None,
            "outcome": outcome, "made_it": outcome in ("made_it", "other_side"),
            "in_time": bool(arrived_school and (by is None or arrived_school <= by)), "words": words,
            "turned": {**turned, **turn_place} if turned else None, "off_route": [
                {"left_at": r["left_at"], "after": place_name(stops, r["left_m"])["words"], "rejoined_at": r["rejoined_at"],
                 "rejoined": place_name(stops, r["rejoined_m"])["words"] if r["rejoined_m"] is not None else None,
                 "missed": [names.get(s, s) for s in r["missed"]], "furthest_off_m": round(r["furthest_off_m"])}
                for r in seen["off_route"]],
            "capacity_first": first_row["capacity"], "capacity_school": school_row["capacity"],
            "delay": late, "stops": rows, "events": events}  # fmt: skip


def reliability(days: list[dict[str, Any]]) -> dict[str, Any]:
    """Over days ({day, summary}): each day's figures and the totals."""
    keys = ("timetabled", "recorded", "made_it", "other_side", "in_time", "turned", "skipped", "lost", "not_seen", "no_data")
    total = {k: sum(d["summary"].get(k) or 0 for d in days) for k in keys}
    total["percent"] = round(100 * total["made_it"] / total["recorded"]) if total["recorded"] else None
    total["in_time_percent"] = round(100 * total["in_time"] / total["recorded"]) if total["recorded"] else None
    return {"days": days, "total": total}

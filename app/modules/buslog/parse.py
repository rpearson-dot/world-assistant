"""Reading what TfL and BODS send, without deciding anything: each answer becomes rows that keep every field. Pure: no
network, no database, so it is tested against small hand-written answers.

- TfL arrivals (/Line/{line}/Arrivals): one prediction per bus per stop.
- BODS SIRI-VM: one position per bus; every element of its journey kept in `fields`.
- TfL status (/Line/{line}/Status), route (/Line/{line}/Route/Sequence/{direction}) and timetable
  (/Line/{line}/Timetable/{stop}?direction=).
- `changes`: which predictions are new, have moved, or have gone since the last answer.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
MOVE_S = 15  # a prediction that shifts by less than this is the same prediction
MAX_XML = 20 * 1024 * 1024


def when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- TfL arrivals ------------------------------------------------------------------------------------------------


def arrivals(body: Any) -> list[dict[str, Any]]:
    """TfL's predictions, one per bus and stop. Raises ValueError when the answer is not the expected list."""
    if not isinstance(body, list):
        raise ValueError("TfL arrivals: expected a list")  # noqa: TRY004 - callers catch ValueError for any bad answer
    out = []
    for r in body:
        if not isinstance(r, dict) or not r.get("vehicleId") or not r.get("naptanId"):
            continue
        out.append({
            "vehicle": str(r["vehicleId"]), "stop": str(r["naptanId"]), "stop_name": r.get("stationName"),
            "direction": r.get("direction"), "destination": r.get("destinationName"), "towards": r.get("towards"),
            "expected_at": when(r.get("expectedArrival")), "time_to_station": _int(r.get("timeToStation")),
            "computed_at": when(r.get("timestamp")), "current_location": r.get("currentLocation") or None,
            "fields": r,
        })  # fmt: skip
    return out


def key_of(row: dict[str, Any]) -> str:
    return f"{row['vehicle']}|{row['stop']}"


def changes(previous: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """What has changed since the last answer, and the state to keep for the next one.

    previous: {vehicle|stop: {"expected": iso, ...}} from the last answer. Each row is "new" (first predicted), "moved"
    (by MOVE_S or more; moved_s is how far, later is positive) or, for one that is no longer predicted, "gone" (it
    arrived, or TfL dropped it: which, the record cannot say)."""
    out: list[dict[str, Any]] = []
    state: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for r in rows:
        k = key_of(r)
        if k in seen:
            continue
        seen.add(k)
        exp = r["expected_at"]
        state[k] = {"expected": exp.isoformat() if exp else None, "stop_name": r["stop_name"], "direction": r["direction"],
                    "destination": r["destination"], "towards": r["towards"]}  # fmt: skip
        before = previous.get(k)
        if before is None:
            out.append({**r, "change": "new", "previous_at": None, "moved_s": None})
            continue
        was = when(before.get("expected"))
        if exp is not None and was is not None and abs((exp - was).total_seconds()) >= MOVE_S:
            out.append({**r, "change": "moved", "previous_at": was, "moved_s": round((exp - was).total_seconds())})
    for k, before in previous.items():
        if k not in seen:
            vehicle, stop = k.split("|", 1)
            out.append({"vehicle": vehicle, "stop": stop, "stop_name": before.get("stop_name"),
                        "direction": before.get("direction"), "destination": before.get("destination"),
                        "towards": before.get("towards"), "expected_at": None, "time_to_station": None,
                        "computed_at": None, "current_location": None, "fields": {},
                        "change": "gone", "previous_at": when(before.get("expected")), "moved_s": None})  # fmt: skip
    return out, state


# --- BODS SIRI-VM ------------------------------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _flatten(el: ET.Element, prefix: str = "") -> dict[str, str]:
    """Every element under one, by its path ("VehicleLocation.Latitude"); repeated paths get .2, .3..."""
    out: dict[str, str] = {}
    for child in el:
        name = (prefix + "." if prefix else "") + _local(child.tag)
        if len(child):
            for k, v in _flatten(child, name).items():
                key, n = k, 2
                while key in out:
                    key, n = f"{k}.{n}", n + 1
                out[key] = v
        elif child.text and child.text.strip():
            key, n = name, 2
            while key in out:
                key, n = f"{name}.{n}", n + 1
            out[key] = child.text.strip()
    return out


def positions(body: bytes, line: str) -> list[dict[str, Any]]:
    """BODS positions of the line's buses, every field kept. Raises ValueError on an answer that is not SIRI-VM XML."""
    if len(body) > MAX_XML:
        raise ValueError("BODS answer larger than 20 MB")
    low = body[:4096].lower()
    if b"<!doctype" in low or b"<!entity" in low:
        raise ValueError("BODS answer has a DTD or entity, which is never expected")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"BODS answer is not XML ({exc})") from exc
    out = []
    for act in root.iter():
        if _local(act.tag) != "VehicleActivity":
            continue
        f = _flatten(act)
        j = {k[len("MonitoredVehicleJourney."):]: v for k, v in f.items() if k.startswith("MonitoredVehicleJourney.")}
        name = (j.get("PublishedLineName") or j.get("LineRef") or "").strip()
        vehicle = j.get("VehicleRef")
        if name.upper() != line.upper() or not vehicle:
            continue
        out.append({
            "vehicle": vehicle, "recorded_at": when(f.get("RecordedAtTime")), "valid_until": when(f.get("ValidUntilTime")),
            "lat": _float(j.get("VehicleLocation.Latitude")), "lon": _float(j.get("VehicleLocation.Longitude")),
            "bearing": _float(j.get("Bearing")), "direction": j.get("DirectionRef"),
            "journey_ref": j.get("FramedVehicleJourneyRef.DatedVehicleJourneyRef") or j.get("VehicleJourneyRef"),
            "block_ref": j.get("BlockRef"), "origin_ref": j.get("OriginRef"), "origin_name": j.get("OriginName"),
            "origin_aimed_departure": when(j.get("OriginAimedDepartureTime")), "destination_ref": j.get("DestinationRef"),
            "destination_name": j.get("DestinationName"), "operator": j.get("OperatorRef"),
            "occupancy": j.get("Occupancy"), "fields": f,
        })  # fmt: skip
    return out


# --- TfL status, route and timetable ----------------------------------------------------------------------------


def status(body: Any) -> dict[str, Any]:
    """The line's status: the worst severity, its reason, and every disruption as TfL gave it."""
    lines = body if isinstance(body, list) else [body]
    statuses = [s for ln in lines if isinstance(ln, dict) for s in ln.get("lineStatuses") or []]
    if not statuses:
        return {"severity": None, "reason": None, "disruptions": []}
    worst = min(statuses, key=lambda s: s.get("statusSeverity", 10))
    return {"severity": worst.get("statusSeverityDescription"), "reason": worst.get("reason"),
            "disruptions": [s.get("disruption") or {"reason": s.get("reason")} for s in statuses if s.get("statusSeverity", 10) != 10]}  # fmt: skip


def route(body: dict[str, Any], stops_at: dict[str, tuple[float, float, str]]) -> dict[str, Any]:
    """TfL's stop order and line shape for one direction. Stops take NaPTAN's position where it has one (stops_at:
    atco -> (lat, lon, name)), else TfL's."""
    order: list[str] = []
    for r in body.get("orderedLineRoutes") or []:
        for sid in r.get("naptanIds") or []:
            if sid not in order:
                order.append(sid)
    tfl: dict[str, dict[str, Any]] = {}
    for seq in body.get("stopPointSequences") or []:
        for sp in seq.get("stopPoint") or []:
            sid = sp.get("id") or sp.get("stationId")
            if sid:
                tfl[sid] = sp
                if sid not in order:
                    order.append(sid)
    stops = []
    for i, sid in enumerate(order):
        n = stops_at.get(sid)
        sp = tfl.get(sid, {})
        lat, lon = (n[0], n[1]) if n else (_float(sp.get("lat")), _float(sp.get("lon")))
        stops.append({"seq": i, "stop": sid, "name": (n[2] if n else None) or sp.get("name"), "lat": lat, "lon": lon,
                      "from": "naptan" if n else "tfl", "letter": sp.get("stopLetter")})  # fmt: skip
    lines = []
    for item in body.get("lineStrings") or []:
        pts = json.loads(item) if isinstance(item, str) else item
        if pts and isinstance(pts[0], list) and pts[0] and isinstance(pts[0][0], (int, float)):
            pts = [pts]
        lines += [p for p in pts if isinstance(p, list) and len(p) >= 2]
    wkt = None
    if lines:
        wkt = "MULTILINESTRING(" + ",".join("(" + ",".join(f"{x} {y}" for x, y, *_ in ln) + ")" for ln in lines) + ")"
    return {"stops": stops, "shape_wkt": wkt}


DAY_WORDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _covers(name: str, weekday: int) -> int:
    """How well a schedule's name ("Monday to Friday", "Saturday", "Mon-Thu School Days") covers a weekday: 2 named,
    1 inside a named range, 0 not at all."""
    n = name.lower().replace("-", " to ").replace("mon ", "monday ").replace("fri", "friday").replace("thu", "thursday")
    days = [i for i, d in enumerate(DAY_WORDS) if d in n or d[:3] + " " in n + " "]
    if weekday in days:
        return 2
    if " to " in n and len(days) >= 2 and days[0] <= weekday <= days[-1]:
        return 1
    return 0


def timetable(body: dict[str, Any], day: date) -> tuple[str | None, list[dict[str, Any]]]:
    """The scheduled time at every stop of every journey on `day`, from TfL's timetable from one end: the schedule
    whose name covers the day's weekday, its journeys (hour and minute at the first stop) plus each stop's interval."""
    tt = body.get("timetable") or {}
    names = {s.get("id"): s.get("name") for s in body.get("stops") or []}
    rows: list[dict[str, Any]] = []
    chosen = None
    for r in tt.get("routes") or []:
        intervals = {str(iv.get("id")): iv.get("intervals") or [] for iv in r.get("stationIntervals") or []}
        scheds = r.get("schedules") or []
        best = max(scheds, key=lambda s: _covers(s.get("name") or "", day.weekday()), default=None)
        if not best or _covers(best.get("name") or "", day.weekday()) == 0:
            continue
        chosen = best.get("name")
        start = datetime(day.year, day.month, day.day, tzinfo=LONDON)
        first = tt.get("departureStopId") or body.get("departureStopId")
        for j, kj in enumerate(best.get("knownJourneys") or []):
            h, m = _int(kj.get("hour")), _int(kj.get("minute"))
            if h is None or m is None:
                continue
            leaves = start + timedelta(hours=h, minutes=m)
            stops = [{"stopId": first, "timeToArrival": 0}] if first else []
            stops += intervals.get(str(kj.get("intervalId")), [])  # TfL: "0" here, 0 there
            for seq, iv in enumerate(stops):
                at = leaves + timedelta(minutes=float(iv.get("timeToArrival") or 0))
                rows.append({"journey": j, "stop": iv.get("stopId"), "stop_name": names.get(iv.get("stopId")),
                             "stop_seq": seq, "scheduled_at": at.astimezone(UTC)})  # fmt: skip
    return chosen, rows

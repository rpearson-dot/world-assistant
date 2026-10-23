"""A bus route's stops and weekday times from a BODS timetable file (TransXChange, "TXC").

BODS publishes each operator's timetables as TransXChange XML. For one line this reads the order of the
stops in each direction and the Monday-to-Friday departure times, which is all the bus tracker needs from a
timetable. Nothing here touches the network.

Read from the TransXChange 2.4 schema (JourneyPatternSection timing links, VehicleJourney departure times).
Not run against the live service from where this was written; tested on files built to that shape. The
parser ignores XML namespaces and anything it does not know, and a file for another line returns None.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # a DOCTYPE is refused before parsing
from dataclasses import dataclass, field

MAX_BYTES = 80 * 1024 * 1024  # a big operator's file; more means something is wrong

_DURATION = re.compile(r"^P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")
_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "mondaytofriday",
             "mondaytosaturday", "mondaytosunday", "notsaturday", "notsunday", "notweekend"}  # fmt: skip
_DIRECTIONS = {"outbound": "outbound", "inbound": "inbound", "clockwise": "outbound", "anticlockwise": "inbound",
               "circular": "outbound"}  # fmt: skip


class TxcError(Exception):
    """The file could not be read as a timetable."""


@dataclass
class Pattern:
    direction: str
    stops: list[str] = field(default_factory=list)  # ATCO codes, in order
    offsets: list[int] = field(default_factory=list)  # seconds after leaving the first stop, one per stop


@dataclass
class Route:
    line: str
    names: dict[str, str] = field(default_factory=dict)  # ATCO code -> stop name
    patterns: dict[str, Pattern] = field(default_factory=dict)
    journeys: list[tuple[str, int]] = field(default_factory=list)  # (pattern id, minute of day), weekdays only

    def sequences(self) -> dict[str, list[dict[str, str]]]:
        """{direction: [{id, name}]}: the longest pattern each way, as the TfL route sequence is."""
        best: dict[str, Pattern] = {}
        for p in self.patterns.values():
            if len(p.stops) > len(best[p.direction].stops if p.direction in best else []):
                best[p.direction] = p
        return {d: [{"id": s, "name": self.names.get(s, "")} for s in best[d].stops] if d in best else []
                for d in ("outbound", "inbound")}  # fmt: skip

    def times_at(self, stop: str, direction: str) -> list[str]:
        """Weekday times ('HH:MM') at a stop going this way, sorted."""
        out: set[int] = set()
        for pid, minute in self.journeys:
            p = self.patterns.get(pid)
            if p is None or p.direction != direction or stop not in p.stops:
                continue
            out.add((minute + round(p.offsets[p.stops.index(stop)] / 60)) % 1440)
        return [f"{m // 60:02d}:{m % 60:02d}" for m in sorted(out)]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(e: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in e if _local(c.tag) == name]


def _first(e: ET.Element | None, *path: str) -> ET.Element | None:
    for name in path:
        if e is None:
            return None
        found = _kids(e, name)
        e = found[0] if found else None
    return e


def _pick(first: ET.Element | None, otherwise: ET.Element | None) -> ET.Element | None:
    """`first` when there is one. (An element with no children is falsy, so `or` would be wrong.)"""
    return first if first is not None else otherwise


def _text(e: ET.Element | None) -> str:
    return (e.text or "").strip() if e is not None else ""


def seconds(duration: str) -> int:
    """'PT2M30S' -> 150. Anything unreadable is 0."""
    m = _DURATION.match(duration.strip()) if duration else None
    if not m:
        return 0
    return round(int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0))


def _clock(value: str) -> int | None:
    parts = value.split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (IndexError, ValueError):
        return None


def _is_weekday(profile: ET.Element | None) -> bool:
    """No operating profile means every day. Otherwise it must name a weekday (or 'MondayToFriday', ...)."""
    if profile is None:
        return True
    days = _first(profile, "RegularDayType", "DaysOfWeek")
    if days is None:
        return True
    names = {_local(c.tag).lower() for c in days}
    return not names or bool(names & _WEEKDAYS)


def parse_txc(body: bytes, line: str) -> Route | None:
    """The route for `line` from one TXC document, or None when the file has no such line."""
    if len(body) > MAX_BYTES:
        raise TxcError("the timetable file was larger than expected")
    lowered = body[:4096].lower() + body[-4096:].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise TxcError("the timetable file carried a DOCTYPE, which it should not")
    try:
        root = ET.fromstring(body)  # DOCTYPE and entities were refused above
    except ET.ParseError as exc:
        raise TxcError(f"the timetable file was not valid XML: {exc}") from exc
    want = line.strip().upper()
    names: dict[str, str] = {}
    for ref in root.iter():
        if _local(ref.tag) == "AnnotatedStopPointRef":
            code, name = _text(_first(ref, "StopPointRef")), _text(_first(ref, "CommonName"))
            if code:
                names[code] = name
    sections: dict[str, list[tuple[str, str, int]]] = {}  # id -> [(from, to, seconds including waits)]
    for sec in root.iter():
        if _local(sec.tag) != "JourneyPatternSection":
            continue
        links = []
        for link in _kids(sec, "JourneyPatternTimingLink"):
            a, b = _text(_first(link, "From", "StopPointRef")), _text(_first(link, "To", "StopPointRef"))
            wait = seconds(_text(_first(link, "To", "WaitTime")))
            links.append((a, b, seconds(_text(_first(link, "RunTime"))) + wait))
        sections[sec.get("id") or ""] = links
    route = Route(line=line, names=names)
    service_profile: ET.Element | None = None
    line_ids: set[str] = set()
    for service in root.iter():
        if _local(service.tag) != "Service":
            continue
        mine = {ln.get("id") or "" for ln in _kids(_pick(_first(service, "Lines"), service), "Line")
                if _text(_first(ln, "LineName")).upper() == want}  # fmt: skip
        if not mine:
            continue
        line_ids |= mine
        service_profile = _first(service, "OperatingProfile")
        for jp in _pick(_first(service, "StandardService"), service).iter():
            if _local(jp.tag) != "JourneyPattern":
                continue
            direction = _DIRECTIONS.get(_text(_first(jp, "Direction")).lower(), "outbound")
            pattern = Pattern(direction)
            elapsed = 0
            for ref in _kids(jp, "JourneyPatternSectionRefs"):
                for link in sections.get(_text(ref), []):
                    if not pattern.stops:
                        pattern.stops.append(link[0])
                        pattern.offsets.append(0)
                    elapsed += link[2]
                    pattern.stops.append(link[1])
                    pattern.offsets.append(elapsed)
            if len(pattern.stops) >= 2:
                route.patterns[jp.get("id") or ""] = pattern
    if not line_ids or not route.patterns:
        return None
    for vj in root.iter():
        if _local(vj.tag) != "VehicleJourney":
            continue
        pid, minute = _text(_first(vj, "JourneyPatternRef")), _clock(_text(_first(vj, "DepartureTime")))
        lref = _text(_first(vj, "LineRef"))
        if pid not in route.patterns or minute is None or (lref and line_ids and lref not in line_ids):
            continue
        if _is_weekday(_pick(_first(vj, "OperatingProfile"), service_profile)):
            route.journeys.append((pid, minute))
    return route

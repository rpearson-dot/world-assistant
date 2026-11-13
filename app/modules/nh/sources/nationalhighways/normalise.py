"""Turn National Highways DATEX II payloads into plain, typed records.

Pure functions, no network or database. Works on the `D2Payload` object of the
closures, speed managed areas and digital VMS feeds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

KMH_PER_MPH = 1.609344
_PLACEHOLDER = re.compile(r"\[[a-z]{3}\d+\]")

Point = tuple[float, float]  # (lat, lon)


@dataclass(frozen=True)
class Segment:
    """One stretch of one road: a line of points plus road and direction."""

    road: str | None
    direction: str | None
    description: str | None
    points: tuple[Point, ...]


@dataclass(frozen=True)
class RoadEvent:
    kind: str  # "closure" or "speed"
    record_id: str
    situation_id: str | None
    status: str | None  # active, planned, suspended, ...
    start: datetime | None
    end: datetime | None
    cause: str | None
    detail: str | None  # closure type, or "mandatory"/"advisory" for speed
    limit_mph: int | None  # speed events only; None means no limit shown
    segments: tuple[Segment, ...]
    version_time: datetime | None = None

    @property
    def roads(self) -> set[str]:
        return {s.road for s in self.segments if s.road}


@dataclass(frozen=True)
class VmsSign:
    sign_id: str
    road: str | None
    direction: str | None
    description: str | None
    lat: float | None
    lon: float | None
    working: bool
    time_last_set: datetime | None
    lines: tuple[str, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        return " | ".join(self.lines)


def parse_time(value: str | None) -> datetime | None:
    """Parse the feed's timestamps, which use 2-digit fractions like '.36Z'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_pos_list(pos_list: str | None) -> tuple[Point, ...]:
    """'lat lon lat lon ...' to a tuple of (lat, lon)."""
    if not pos_list:
        return ()
    nums = pos_list.split()
    try:
        values = [float(n) for n in nums]
    except ValueError:
        return ()
    return tuple(zip(values[0::2], values[1::2], strict=False))


def kmh_to_mph(kmh: float | None) -> int | None:
    """Feed limits are km/h; 0 means no limit is being shown."""
    if not kmh:
        return None
    return round(kmh / KMH_PER_MPH)


def _segments_from_part(part: dict[str, Any]) -> Iterator[Segment]:
    lin = part.get("locLinearLocation") or {}
    line = ((lin.get("gmlLineString") or {}).get("locGmlLineString") or {}).get("posList")
    description = (lin.get("supplementaryPositionalDescription") or {}).get("locationDescription")
    points = parse_pos_list(line)
    single = part.get("locSingleRoadLinearLocation") or {}
    elements = single.get("linearWithinLinearElement") or []
    if not elements:
        if points:
            yield Segment(None, None, description, points)
        return
    for el in elements:
        code = (el.get("linearElement") or {}).get("locLinearElementByCode") or {}
        yield Segment(code.get("roadName"), el.get("directionOnLinearSection"), description, points)


def segments_of(location: dict[str, Any]) -> tuple[Segment, ...]:
    """All segments in a record's locationReference (single or grouped)."""
    group = (location.get("locLocationGroupByList") or {}).get("locationContainedInGroup")
    parts = group if group else [location]
    out: list[Segment] = []
    for part in parts:
        out.extend(_segments_from_part(part))
    return tuple(out)


def _cause(rec: dict[str, Any]) -> str | None:
    return (rec.get("cause") or {}).get("causeType")


def _closure_detail(rec: dict[str, Any]) -> str | None:
    typ = rec.get("roadOrCarriagewayOrLaneManagementType")
    if isinstance(typ, dict):
        return typ.get("value")
    return typ if isinstance(typ, str) else None


def iter_events(payload: dict[str, Any]) -> Iterator[RoadEvent]:
    """Yield one RoadEvent per situation record in a closures or speed payload."""
    for sit in payload.get("situation") or []:
        sit_id = sit.get("idG")
        for wrapper in sit.get("situationRecord") or []:
            for key, rec in wrapper.items():
                validity = rec.get("validity") or {}
                span = validity.get("validityTimeSpecification") or {}
                is_speed = key == "sitSpeedManagement"
                yield RoadEvent(
                    kind="speed" if is_speed else "closure",
                    record_id=rec.get("idG", ""),
                    situation_id=sit_id,
                    status=validity.get("validityStatus"),
                    start=parse_time(span.get("overallStartTime")),
                    end=parse_time(span.get("overallEndTime")),
                    cause=_cause(rec),
                    detail=rec.get("complianceOption") if is_speed else _closure_detail(rec),
                    limit_mph=kmh_to_mph(rec.get("temporarySpeedLimit")) if is_speed else None,
                    segments=segments_of(rec.get("locationReference") or {}),
                    version_time=parse_time(rec.get("situationRecordVersionTime")),
                )


def clean_sign_line(line: str) -> str:
    """Drop pictogram placeholders like [hca1] and tidy spacing."""
    return _PLACEHOLDER.sub("", line).strip()


def iter_vms(payload: dict[str, Any]) -> Iterator[VmsSign]:
    """Yield one VmsSign per controller status in a digital VMS payload."""
    for controller in payload.get("vmsControllerStatus") or []:
        ref = controller.get("vmsControllerReference") or {}
        for entry in controller.get("vmsStatus") or []:
            st = entry.get("vmsStatus") or {}
            ext = st.get("vmsStatusExtensionG") or {}
            point = (ext.get("vmsLocation") or {}).get("locPointLocation") or {}
            coords = (point.get("pointByCoordinates") or {}).get("pointCoordinates") or {}
            sup = point.get("supplementaryPositionalDescription") or {}
            roads = sup.get("roadInformation") or []
            direction = (sup.get("supplementaryPositionalDescriptionExtensionG") or {}).get(
                "direction"
            )
            lines: list[str] = []
            last_set: datetime | None = None
            for m in st.get("vmsMessage") or []:
                msg = m.get("vmsMessage") or {}
                last_set = parse_time(msg.get("timeLastSet")) or last_set
                for area in msg.get("displayAreaSettings") or []:
                    text = (area.get("displayAreaSettings") or {}).get("vmsTextDisplay")
                    for ln in (text or {}).get("textLine") or []:
                        cleaned = clean_sign_line((ln.get("textLine") or {}).get("textLine", ""))
                        if cleaned:
                            lines.append(cleaned)
            yield VmsSign(
                sign_id=ref.get("idG", ""),
                road=roads[0].get("roadName") if roads else None,
                direction=direction,
                description=sup.get("locationDescription"),
                lat=coords.get("latitude"),
                lon=coords.get("longitude"),
                working=(st.get("workingStatus") or "").strip() == "working",
                time_last_set=last_set,
                lines=tuple(lines),
            )


def parse_events(payloads: Iterable[dict[str, Any]]) -> list[RoadEvent]:
    return [e for p in payloads for e in iter_events(p)]


def parse_vms(payloads: Iterable[dict[str, Any]]) -> list[VmsSign]:
    return [s for p in payloads for s in iter_vms(p)]

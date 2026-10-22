"""TfL road disruptions into what we store. Pure, so every rule is testable alone.

The Unified API's `RoadDisruption` is not fully documented, so this takes what it can find and tolerates what it
cannot: a disruption with no usable position is kept (it still reads in a list) but cannot be matched to a route.
Where it is comes from, in order of preference: the lines of its affected streets, the `geometry` GeoJSON, the
`geography` GeoJSON point, then the `point` field. Coordinates are [lon, lat], as in GeoJSON.

    python -m app.integrations.tflroads.probe     # prints the real shape from your key
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

UNPLANNED = re.compile(r"collision|incident|accident|breakdown|obstruct|hazard|emergency|flood|congestion|fire|"
                       r"spillage|burst|sinkhole|signal fail", re.IGNORECASE)  # fmt: skip
SEVERITIES = ("Minimal", "Moderate", "Serious", "Severe")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def parse_time(value: Any, tz: str = "Europe/London") -> datetime | None:
    """A timestamp from the feed. One without an offset is read in London time."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00") if value.strip().endswith("Z") else value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed.astimezone(UTC)


def _valid(lon: Any, lat: Any) -> tuple[float, float] | None:
    try:
        lo, la = float(lon), float(lat)
    except (TypeError, ValueError):
        return None
    return (lo, la) if -9.0 <= lo <= 3.0 and 49.0 <= la <= 61.0 else None  # the British Isles; the rest is noise


def _pairs(raw: Any) -> list[tuple[float, float]]:
    """A list of [lon, lat] pairs, given as a list or as a JSON string of one."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    out: list[tuple[float, float]] = []
    if isinstance(raw, list):
        for p in raw:
            if isinstance(p, (list, tuple)) and len(p) >= 2 and (v := _valid(p[0], p[1])):
                out.append(v)
    return out


def _ring(raw: Any) -> list[tuple[float, float]] | None:
    """A polygon's outer ring, closed, or None if it is too small to be an area."""
    ring = _pairs(raw)
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring if len(ring) >= 4 else None


def _geojson_parts(
    geo: Any,
    lines: list[list[tuple[float, float]]],
    points: list[tuple[float, float]],
    areas: list[list[tuple[float, float]]],
) -> None:
    if not isinstance(geo, dict):
        return
    kind, coords = geo.get("type"), geo.get("coordinates")
    if kind == "Point" and isinstance(coords, list) and len(coords) >= 2 and (v := _valid(coords[0], coords[1])):
        points.append(v)
    elif kind == "LineString" and (ln := _pairs(coords)):
        lines.append(ln)
    elif kind == "MultiLineString" and isinstance(coords, list):
        lines.extend(ln for part in coords if (ln := _pairs(part)))
    elif kind == "Polygon" and isinstance(coords, list) and coords and (ring := _ring(coords[0])):
        areas.append(ring)
    elif kind == "MultiPolygon" and isinstance(coords, list):
        # TfL's "geometry": the area the works affect, one polygon per stretch of road (outer rings only)
        areas.extend(ring for poly in coords if isinstance(poly, list) and poly and (ring := _ring(poly[0])))
    elif kind == "GeometryCollection":
        for g in geo.get("geometries") or []:
            _geojson_parts(g, lines, points, areas)


def geometry_of(item: dict[str, Any]) -> dict[str, Any] | None:
    """GeoJSON for where the disruption is, most exact first.

    Street lines (a MultiLineString) when TfL gives them; else the affected area (a MultiPolygon, from
    "geometry"); else any other line or point the feed carries; else the single "point". Only about one in
    five items has street lines; the area lets the rest match along their length, not at one point.
    """
    lines: list[list[tuple[float, float]]] = []
    points: list[tuple[float, float]] = []
    areas: list[list[tuple[float, float]]] = []
    for street in item.get("streets") or []:
        if not isinstance(street, dict):
            continue
        for seg in street.get("segments") or []:
            if isinstance(seg, dict) and (ln := _pairs(seg.get("lineString"))):
                lines.append(ln)
    if not lines:
        _geojson_parts(item.get("geometry"), lines, points, areas)
    if not lines and not areas:
        _geojson_parts(item.get("geography"), lines, points, areas)
    if not lines and not areas and not points:
        raw = item.get("point")
        if isinstance(raw, str):
            nums = re.findall(r"-?\d+(?:\.\d+)?", raw)
            if len(nums) >= 2 and (v := _valid(nums[0], nums[1])):
                points.append(v)
    lines = [ln for ln in lines if len(ln) >= 2]
    if lines:
        return {"type": "MultiLineString", "coordinates": [[list(p) for p in ln] for ln in lines]}
    if areas:
        return {"type": "MultiPolygon", "coordinates": [[[list(p) for p in ring]] for ring in areas]}
    if points:
        return {"type": "Point", "coordinates": list(points[0])}
    return None


def scope_of(category: str | None, sub_category: str | None) -> str:
    """Unplanned (an incident) or planned (works and events). Words, not a fixed list: TfL's categories drift."""
    return "unplanned" if UNPLANNED.search(f"{category or ''} {sub_category or ''}") else "planned"


def parse_item(item: dict[str, Any], tz: str = "Europe/London") -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    ident = _text(item.get("id"))
    if not ident:
        return None
    category, sub = _text(item.get("category")), _text(item.get("subCategory"))
    severity = _text(item.get("severity"))
    closures = bool(item.get("hasClosures"))
    if not closures:
        closures = any(
            isinstance(s, dict) and str(s.get("closure") or "").lower() not in ("", "open", "none")
            for s in item.get("streets") or []
        )
    corridors = item.get("corridorIds")
    return {
        "id": ident, "category": category, "sub_category": sub, "severity": severity,
        "status": _text(item.get("status")), "scope": scope_of(category, sub),
        "location": _text(item.get("location")), "comment": _text(item.get("comments")),
        "update_text": _text(item.get("currentUpdate")),
        "starts_at": parse_time(item.get("startDateTime"), tz), "ends_at": parse_time(item.get("endDateTime"), tz),
        "has_closures": closures,
        "corridors": ",".join(str(c) for c in corridors) if isinstance(corridors, list) and corridors else None,
        "last_modified": parse_time(item.get("lastModifiedTime"), tz),
        "geom": geometry_of(item),
    }  # fmt: skip


def rank(severity: str | None) -> int:
    """0 none known, 1 minimal .. 4 severe."""
    try:
        return SEVERITIES.index(str(severity).strip().title()) + 1
    except ValueError:
        return 0

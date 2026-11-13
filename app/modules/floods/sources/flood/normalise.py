"""Environment Agency flood-warning items into what we store. Pure, so the rules are testable alone.

Severity levels: 1 severe warning, 2 warning, 3 alert, 4 no longer in force. The API keeps a warning
at level 4 for about a day after it ends, so level 4 is stored (it explains a warning that just
vanished) but never drawn or counted as live.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

LIVE_LEVELS = (1, 2, 3)
LEVEL_NAMES = {1: "Severe flood warning", 2: "Flood warning", 3: "Flood alert", 4: "No longer in force"}


def parse_time(value: Any, tz: str = "Europe/London") -> datetime | None:
    """A timestamp from the feed. They carry no offset, so they are read in the zone the EA is in."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed.astimezone(UTC)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def parse_item(item: dict[str, Any], tz: str = "Europe/London") -> dict[str, Any] | None:
    """One entry of `/id/floods` as (warning, area), or None if it cannot be used."""
    if not isinstance(item, dict):
        return None
    area = item.get("floodArea") if isinstance(item.get("floodArea"), dict) else {}
    warning_id = _text(item.get("floodAreaID")) or _text(area.get("notation"))
    notation = _text(area.get("notation")) or warning_id
    try:
        level = int(item.get("severityLevel"))
    except (TypeError, ValueError):
        return None
    if not warning_id or not notation or level not in LEVEL_NAMES:
        return None
    return {
        "warning": {
            "id": warning_id,
            "area": notation,
            "severity_level": level,
            "severity": _text(item.get("severity")) or LEVEL_NAMES[level],
            "message": _text(item.get("message")),
            "is_tidal": item.get("isTidal") if isinstance(item.get("isTidal"), bool) else None,
            "raised_at": parse_time(item.get("timeRaised"), tz),
            "severity_changed_at": parse_time(item.get("timeSeverityChanged"), tz),
            "message_changed_at": parse_time(item.get("timeMessageChanged"), tz),
        },
        "area": {
            "notation": notation,
            "label": _text(item.get("description")),
            "county": _text(area.get("county")),
            "river_or_sea": _text(area.get("riverOrSea")),
            "ea_area": _text(item.get("eaAreaName")),
            "polygon_url": _text(area.get("polygon")),
        },
    }


def geometry_of(body: Any) -> dict[str, Any] | None:
    """The polygon from a boundary response, which may be a Feature or a FeatureCollection."""
    if not isinstance(body, dict):
        return None
    if body.get("type") == "FeatureCollection":
        features = body.get("features") or []
        body = features[0] if features and isinstance(features[0], dict) else {}
    if body.get("type") == "Feature":
        body = body.get("geometry") or {}
    if isinstance(body, dict) and body.get("type") in ("Polygon", "MultiPolygon") and body.get("coordinates"):
        return body
    return None

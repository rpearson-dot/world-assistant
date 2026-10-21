"""Turn TfL ArrivalDepartures records into departures. Parsing is deliberately tolerant:
the field names below are the documented ones, and anything missing becomes None."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.integrations.rail.model import Departure


def parse_time(value: Any, naive_tz: str = "Europe/London") -> datetime | None:
    """ISO time to an aware datetime. A time with no zone is read as naive_tz local time."""
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=ZoneInfo(naive_tz)) if dt.tzinfo is None else dt.astimezone(UTC)


def _s(rec: dict[str, Any], *keys: str) -> str | None:
    for k in keys:
        v = rec.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def parse_departures(records: Sequence[dict[str, Any]], naive_tz: str = "Europe/London") -> list[Departure]:
    out = []
    for r in records:
        out.append(
            Departure(
                station=_s(r, "stationName"),
                line=_s(r, "lineName", "lineId"),
                destination=_s(r, "destinationName"),
                platform=_s(r, "platformName"),
                scheduled=parse_time(r.get("scheduledTimeOfDeparture"), naive_tz),
                expected=parse_time(r.get("estimatedTimeOfDeparture"), naive_tz),
                status=_s(r, "departureStatus"),
                cause=_s(r, "cause"),
            )
        )
    return out


def select(
    deps: Sequence[Departure], *, include: Sequence[str], exclude: Sequence[str], now: datetime,
    limit: int,
) -> list[Departure]:  # fmt: skip
    """Keep upcoming departures matching the destination filter, soonest first."""
    inc = [s.lower() for s in include if s]
    exc = [s.lower() for s in exclude if s]
    kept = []
    for d in deps:
        dest = (d.destination or "").lower()
        if inc and not any(s in dest for s in inc):
            continue
        if any(s in dest for s in exc):
            continue
        when = d.when
        if when is None or (when - now).total_seconds() < -60:  # already gone
            continue
        kept.append(d)
    kept.sort(key=lambda d: d.when or now)
    return kept[: max(limit, 0)]


def summarise_status(lines: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for ln in lines:
        statuses = ln.get("lineStatuses") or []
        first = statuses[0] if statuses and isinstance(statuses[0], dict) else {}
        out.append({"id": ln.get("id"), "name": ln.get("name"), "mode": ln.get("modeName"),
                    "status": first.get("statusSeverityDescription"),
                    "severity": first.get("statusSeverity"), "reason": first.get("reason")})  # fmt: skip
    return out

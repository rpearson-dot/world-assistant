"""Saving Sessions (and boost events) as Home Assistant wants them: on now, and what is next. Pure."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _iso(d: datetime) -> str:
    return d.isoformat()


# What Octopus calls its event types, in words.
TYPES = {"TURN_DOWN": "Saving Session", "TURN_UP": "Power Up", "WEEKEND_HAPPY_HOUR": "Weekend Happy Hour",
         "FREE_ELECTRICITY": "Free Electricity"}  # fmt: skip


def label(event_type: str | None) -> str:
    return TYPES.get(event_type or "", (event_type or "Free Electricity").replace("_", " ").title())


def _view(s: dict[str, Any], now: datetime) -> dict[str, Any]:
    start, end = s["start_at"], s["end_at"]
    return {"code": s["code"], "start": _iso(start), "end": _iso(end),
            "minutes": round((end - start).total_seconds() / 60),
            "starts_in_minutes": max(0, round((start - now).total_seconds() / 60)),
            "octopoints_per_kwh": s.get("octopoints_per_kwh"), "joined": bool(s.get("joined")),
            "regions": s["regions"].split(",") if s.get("regions") else None,
            "type": label(s.get("event_type"))}  # fmt: skip


def for_kind(rows: list[dict[str, Any]], kind: str, now: datetime) -> dict[str, Any]:
    mine = sorted(
        (r for r in rows if r["kind"] == kind and r["end_at"] > now), key=lambda r: r["start_at"]
    )
    active = [r for r in mine if r["start_at"] <= now < r["end_at"]]
    upcoming = [r for r in mine if r["start_at"] > now]
    joined_up = [r for r in upcoming if r.get("joined")]
    return {
        "now": bool(active),
        "now_joined": any(r.get("joined") for r in active),
        "current": _view(active[0], now) if active else None,
        "next": _view(upcoming[0], now) if upcoming else None,
        "next_joined": _view(joined_up[0], now) if joined_up else None,
        "upcoming": [_view(r, now) for r in upcoming[:10]],
    }


def free_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Free Electricity: the events in the Saving Sessions feed that are not Saving Sessions (Power Ups, Weekend
    Happy Hours; "boost"), which say whether you joined. Rows of kind "free" are merged in too; one session listed
    twice is shown once, joined if either says so."""
    merged: dict[tuple[Any, Any], dict[str, Any]] = {}
    for r in rows:
        if r["kind"] not in ("boost", "free"):
            continue
        key = (r["start_at"], r["end_at"])
        if key in merged:
            merged[key] = {**merged[key], "joined": bool(merged[key].get("joined") or r.get("joined"))}
        else:
            merged[key] = {**r, "kind": "free"}
    return list(merged.values())


def summarise(rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    return {"saving": for_kind(rows, "saving", now), "boost": for_kind(rows, "boost", now),
            "free": for_kind(free_rows(rows), "free", now)}  # fmt: skip

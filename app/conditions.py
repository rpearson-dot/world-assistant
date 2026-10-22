"""What is on the way: road works, closures, incidents and flood warnings along a trip's route, now and planned.

Three sources, each asked about the route line rather than about a point:

- National Highways events tagged to the trip's corridor (motorways and major A-roads),
- TfL road disruptions within `buffer_m` of the route (London's roads),
- Environment Agency flood warnings whose area the route crosses (England).

Everything is read from what the pollers already stored; nothing waits on an upstream service. Each source covers
different roads, so "clear" means nothing *reported* on the route, not that it is empty: there is no live traffic,
and direction of travel is not considered.

`when` is when you set off (default now). "Now" is what affects a journey that starts then and lasts as long as the
route takes; "planned" is what starts after that, within `days`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.integrations.tflroads.normalise import rank

router = APIRouter(prefix="/v1", tags=["conditions"], dependencies=[Depends(require_token)])

STATUSES = ("clear", "minor", "disrupted", "severe")
BUFFER_M = 75.0  # how close a TfL disruption must be to count as on the route
DEFAULT_TRIP_S = 3600  # when the route has no duration yet
FLOODS_VALID_FOR = timedelta(hours=12)  # flood warnings describe the next hours, not next week
SOURCES = ("nh", "tfl", "flood")

# --- the rules (pure) -----------------------------------------------------------------------------


def nh_level(kind: str | None) -> int:
    """A closure is severe for the trip; a speed limit is minor."""
    return 3 if kind == "closure" else 1


def tfl_level(severity: str | None, closure: bool) -> int:
    """1 minor .. 3 severe. TfL's own severity, bumped one step when the road is closed."""
    base = max(rank(severity) - 1, 0)  # Minimal 0, Moderate 1, Serious 2, Severe 3
    return max(1, min(3, base + (1 if closure else 0)))


def flood_level(level: int) -> int:
    """EA severity 3 alert -> 1, 2 warning -> 2, 1 severe warning -> 3."""
    return {1: 3, 2: 2}.get(level, 1)


def status_of(items: list[dict[str, Any]]) -> str:
    return STATUSES[max((i["level"] for i in items), default=0)]


def _iso(x: Any) -> str | None:
    return x.isoformat() if x else None


def nh_item(r: Any) -> dict[str, Any]:
    road = (r.roads or [None])[0]
    if r.kind == "closure":
        head = f"Closure on {road}" if road else "Road closure"
    else:
        head = f"{r.limit_mph} mph limit on {road}" if r.limit_mph and road else "Speed restriction"
    return {"source": "nh", "id": r.record_id, "level": nh_level(r.kind), "headline": head,
            "where": r.description or road, "kind": r.kind, "closure": r.kind == "closure",
            "unplanned": r.scope == "unplanned", "starts": _iso(r.start_time), "ends": _iso(r.end_time),
            "detail": r.detail or r.cause, "status": r.status}  # fmt: skip


def tfl_item(r: Any) -> dict[str, Any]:
    what = r.sub_category if r.sub_category and r.sub_category.lower() != "unknown" else r.category
    return {"source": "tfl", "id": r.id, "level": tfl_level(r.severity, r.has_closures),
            "headline": f"{what or 'Disruption'}" + (" (road closed)" if r.has_closures else ""),
            "where": r.location, "kind": (r.category or "").lower() or None, "closure": r.has_closures,
            "unplanned": r.scope == "unplanned", "starts": _iso(r.starts_at), "ends": _iso(r.ends_at),
            "detail": r.update_text or r.comment, "status": r.status, "severity": r.severity,
            "metres": round(r.metres)}  # fmt: skip


def flood_item(r: Any) -> dict[str, Any]:
    return {"source": "flood", "id": r.id, "level": flood_level(r.severity_level),
            "headline": f"{r.severity} for {r.label}", "where": ", ".join(x for x in (r.river_or_sea, r.county) if x),
            "kind": "flood", "closure": False, "unplanned": True, "starts": _iso(r.raised_at), "ends": None,
            "detail": r.message, "status": "active"}  # fmt: skip


def order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Worst first, then soonest."""
    return sorted(items, key=lambda i: (-i["level"], i["starts"] or ""))


def split(
    items: list[dict[str, Any]], until: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(affecting a journey that starts now and ends at `until`, starting after it)."""
    now_items, later = [], []
    for i in items:
        starts = datetime.fromisoformat(i["starts"]) if i["starts"] else None
        (later if starts and starts > until else now_items).append(i)
    return order(now_items), sorted(later, key=lambda i: (i["starts"] or "", -i["level"]))


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def sentence(name: str, now_items: list[dict[str, Any]], planned: list[dict[str, Any]], days: int,
             tz: Any = UTC, stamp: datetime | None = None) -> str:  # fmt: skip
    """What a person would say aloud."""
    if not now_items:
        out = f"Nothing reported on {name} right now."
    else:
        top = "; ".join(f"{i['headline']}" + (f" at {i['where']}" if i.get("where") else "") for i in now_items[:3])
        more = len(now_items) - 3
        out = (f"{name}: {_count(len(now_items), 'problem')} reported. {top}"
               + (f", and {more} more" if more > 0 else "") + ".")  # fmt: skip
    if planned:
        first = datetime.fromisoformat(planned[0]["starts"]).astimezone(tz)
        out += f" {_count(len(planned), 'planned item')} in the next {days} days; the first starts {first:%A %d %B}."
    return out


# --- asking the database --------------------------------------------------------------------------

ROUTE = """
route AS (
  SELECT ST_MakeLine(ARRAY(
           SELECT ST_SetSRID(ST_MakePoint((p.v->>1)::float8, (p.v->>0)::float8), 4326)
           FROM jsonb_array_elements(c.centreline) WITH ORDINALITY AS p(v, n) ORDER BY p.n
         ))::geography AS line
  FROM nh_corridor c WHERE c.id = :corridor AND jsonb_array_length(c.centreline) > 1
)"""
NH_SQL = """
SELECT e.record_id, e.scope, e.kind, e.status, e.start_time, e.end_time, e.limit_mph, e.detail, e.cause, e.roads,
       e.segments -> 0 ->> 'description' AS description
FROM nh_event e JOIN nh_event_corridor x ON x.record_id = e.record_id AND x.corridor_id = :corridor
WHERE e.cleared_at IS NULL AND coalesce(e.status, '') <> 'suspended'
  AND (e.end_time IS NULL OR e.end_time >= :frm) AND (e.start_time IS NULL OR e.start_time <= :horizon)
"""
TFL_SQL = f"""
WITH {ROUTE}
SELECT d.id, d.category, d.sub_category, d.severity, d.status, d.scope, d.location, d.comment, d.update_text,
       d.starts_at, d.ends_at, d.has_closures, ST_Distance(d.geom, r.line) AS metres
FROM tfl_road_disruption d, route r
WHERE d.geom IS NOT NULL AND ST_DWithin(d.geom, r.line, :buffer)
  AND (d.ends_at IS NULL OR d.ends_at >= :frm) AND (d.starts_at IS NULL OR d.starts_at <= :horizon)
"""
FLOOD_SQL = f"""
WITH {ROUTE}
SELECT w.id, w.severity_level, w.severity, w.message, w.raised_at, a.label, a.county, a.river_or_sea
FROM flood_warning w JOIN flood_area a ON a.notation = w.area, route r
WHERE w.severity_level <= 3 AND a.geom IS NOT NULL AND ST_DWithin(a.geom, r.line, 0)
"""
TRIP_SQL = "SELECT id, name, corridor_id, distance_km, duration_s, roads, enabled FROM trip WHERE id = :id"


async def gather(
    engine: Any, corridor: str, frm: datetime, until: datetime, horizon: datetime, buffer_m: float, floods: bool
) -> list[dict[str, Any]]:
    p = {"corridor": corridor, "frm": frm, "horizon": horizon}
    items: list[dict[str, Any]] = []
    async with engine.connect() as conn:
        items += [nh_item(r) for r in (await conn.execute(text(NH_SQL), p)).all()]
        items += [tfl_item(r) for r in (await conn.execute(text(TFL_SQL), {**p, "buffer": buffer_m})).all()]
        if floods:
            items += [flood_item(r) for r in (await conn.execute(text(FLOOD_SQL), {"corridor": corridor})).all()]
    return items


async def trip_conditions(
    request: Request, trip_id: str, when: datetime | None = None, days: int = 7, buffer_m: float = BUFFER_M
) -> dict[str, Any]:
    from app.config import get_settings

    engine = request.app.state.engine
    async with engine.connect() as conn:
        trip = (await conn.execute(text(TRIP_SQL), {"id": trip_id})).first()
    if trip is None:
        raise HTTPException(404, f"no trip {trip_id!r}")
    if not trip.corridor_id:
        raise HTTPException(409, f"{trip.name} has no route yet: build it first (POST /v1/trips/{trip_id}/route)")

    clock = datetime.now(UTC)
    start = (when if when and when.tzinfo else when.replace(tzinfo=UTC) if when else clock)
    start = max(start, clock - timedelta(minutes=1))  # a departure in the past means now
    until = start + timedelta(seconds=trip.duration_s or DEFAULT_TRIP_S)
    horizon = start + timedelta(days=days)
    floods_apply = start <= clock + FLOODS_VALID_FOR
    items = await gather(engine, trip.corridor_id, start, until, horizon, buffer_m, floods_apply)
    now_items, later = split(items, until)

    from zoneinfo import ZoneInfo

    tz = ZoneInfo(get_settings().local_tz)
    caveats = ["Reports only: there is no live traffic, and direction of travel is not considered."]
    if not floods_apply:
        caveats.append("Flood warnings are not shown for a departure this far ahead.")
    if start > clock + timedelta(hours=1):
        caveats.append("Incidents are what is reported now; new ones will not be known until nearer the time.")
    return {
        "trip": {"id": trip.id, "name": trip.name, "distance_km": trip.distance_km,
                 "duration_min": round((trip.duration_s or DEFAULT_TRIP_S) / 60), "roads": trip.roads or []},
        "when": start.isoformat(), "until": until.isoformat(), "buffer_m": buffer_m,
        "now": {"status": status_of(now_items), "count": len(now_items),
                "by_source": {s: sum(1 for i in now_items if i["source"] == s) for s in SOURCES},
                "items": now_items},
        "planned": {"days": days, "count": len(later), "items": later},
        "summary": sentence(trip.name, now_items, later, days, tz),
        "caveats": caveats,
    }  # fmt: skip


@router.get("/trips/{trip_id}/conditions")
async def conditions(
    trip_id: str,
    request: Request,
    when: Annotated[datetime | None, Query(description="when you set off (ISO 8601); default now")] = None,
    days: int = Query(7, ge=1, le=60, description="how far ahead to look for planned works"),
    buffer_m: float = Query(BUFFER_M, ge=10, le=500, description="how close a TfL disruption must be, in metres"),
) -> dict[str, Any]:
    """Conditions now, and planned works, on a trip's route."""
    return await trip_conditions(request, trip_id, when, days, buffer_m)


@router.get("/conditions")
async def all_conditions(request: Request, days: int = Query(7, ge=1, le=60)) -> dict[str, Any]:
    """Conditions on every enabled trip that has a route; a trip that cannot be read is listed with the reason."""
    async with request.app.state.engine.connect() as conn:
        ids = [r[0] for r in await conn.execute(text("SELECT id FROM trip WHERE enabled ORDER BY name"))]
    out: list[dict[str, Any]] = []
    for tid in ids:
        try:
            out.append(await trip_conditions(request, tid, None, days))
        except HTTPException as exc:
            out.append({"trip": {"id": tid}, "error": str(exc.detail)})
    return {"count": len(out), "trips": out}

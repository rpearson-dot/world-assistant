"""London road disruptions from TfL: works, closures, collisions and incidents, with where they are."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.geoquery import POINT
from app.integrations.tflroads.normalise import rank
from app.places import place_or_default

router = APIRouter(prefix="/v1/roads/tfl", tags=["roads"], dependencies=[Depends(require_token)])

COLUMNS = (
    "id, category, sub_category, severity, status, scope, location, comment, update_text, starts_at, ends_at, "
    "has_closures, corridors"
)
# live now, or about to be: not yet ended (a missing end means "until further notice")
CURRENT = "(ends_at IS NULL OR ends_at >= now())"


def disruption(r: Any) -> dict[str, Any]:
    def iso(x: Any) -> str | None:
        return x.isoformat() if x else None

    return {"id": r.id, "category": r.category, "sub_category": r.sub_category, "severity": r.severity,
            "rank": rank(r.severity), "status": r.status, "scope": r.scope, "location": r.location,
            "comment": r.comment, "update": r.update_text, "starts_at": iso(r.starts_at),
            "ends_at": iso(r.ends_at), "closure": r.has_closures, "corridors": r.corridors}  # fmt: skip


@router.get("")
async def current(
    request: Request,
    scope: str | None = Query(None, pattern="^(planned|unplanned)$"),
    closures_only: bool = False,
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Every current or scheduled disruption, unplanned first, worst first."""
    where = [CURRENT]
    if scope:
        where.append("scope = :scope")
    if closures_only:
        where.append("has_closures")
    sql = (f"SELECT {COLUMNS} FROM tfl_road_disruption WHERE {' AND '.join(where)} "
           "ORDER BY (scope = 'unplanned') DESC, starts_at NULLS LAST LIMIT :limit")  # fmt: skip
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"scope": scope, "limit": limit})).all()
        total = (await conn.execute(text("SELECT count(*) FROM tfl_road_disruption"))).scalar_one()
    items = sorted(
        (disruption(r) for r in rows), key=lambda d: (d["scope"] != "unplanned", -d["rank"])
    )
    return {"count": len(items), "stored": total, "disruptions": items}


@router.get("/at")
async def at(
    request: Request,
    place: str | None = Query(None, description="a saved place id; default: home"),
    lat: float | None = Query(None, ge=49.5, le=61.0),
    lon: float | None = Query(None, ge=-8.7, le=2.0),
    radius_m: float = Query(500.0, ge=10, le=20000),
) -> dict[str, Any]:
    """Disruptions within `radius_m` of a point, nearest first."""
    engine = request.app.state.engine
    name = None
    if lat is None or lon is None:
        pid = await place_or_default(request, place)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT name, lat, lon FROM place WHERE id = :p"), {"p": pid}
                )
            ).first()
        if row is None:
            raise HTTPException(404, f"no place {pid!r}")
        name, lat, lon = row
    sql = (f"SELECT {COLUMNS}, ST_Distance(geom, {POINT}) AS metres FROM tfl_road_disruption "
           f"WHERE {CURRENT} AND geom IS NOT NULL AND ST_DWithin(geom, {POINT}, :m) ORDER BY metres")  # fmt: skip
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"lat": lat, "lon": lon, "m": radius_m})).all()
    return {"place": name, "lat": lat, "lon": lon, "radius_m": radius_m, "count": len(rows),
            "disruptions": [{**disruption(r), "metres": round(r.metres)} for r in rows]}  # fmt: skip

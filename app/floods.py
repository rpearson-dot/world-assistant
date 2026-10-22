"""Flood warnings and alerts from the Environment Agency (England)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.geoquery import POINT
from app.integrations.flood.normalise import LEVEL_NAMES
from app.places import place_or_default

router = APIRouter(prefix="/v1/floods", tags=["floods"], dependencies=[Depends(require_token)])

COLUMNS = (
    "w.id, w.severity_level, w.severity, w.message, w.is_tidal, w.raised_at, w.severity_changed_at, "
    "a.label, a.county, a.river_or_sea, a.ea_area"
)
FROM = "FROM flood_warning w JOIN flood_area a ON a.notation = w.area"


def _warning(r: Any) -> dict[str, Any]:
    def iso(x: Any) -> str | None:
        return x.isoformat() if x else None

    return {"id": r.id, "area": r.label, "level": r.severity_level, "severity": r.severity,
            "message": r.message, "tidal": r.is_tidal, "county": r.county, "river_or_sea": r.river_or_sea,
            "raised_at": iso(r.raised_at), "severity_changed_at": iso(r.severity_changed_at)}  # fmt: skip


@router.get("")
async def current(
    request: Request,
    max_level: int = Query(3, ge=1, le=4, description="1 severe, 2 warning, 3 alert, 4 ended; 3 = everything live"),
) -> dict[str, Any]:
    """Every current warning at least as serious as `max_level`, most serious first."""
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(
            text(f"SELECT {COLUMNS} {FROM} WHERE w.severity_level <= :m "
                 "ORDER BY w.severity_level, a.county, a.label"), {"m": max_level})).all()  # fmt: skip
    return {"count": len(rows), "levels": LEVEL_NAMES, "warnings": [_warning(r) for r in rows]}


@router.get("/at")
async def at(
    request: Request,
    place: str | None = Query(None, description="a saved place id; default: home"),
    lat: float | None = Query(None, ge=49.5, le=61.0),
    lon: float | None = Query(None, ge=-8.7, le=2.0),
    within_km: float = Query(0.0, ge=0, le=50, description="0 = the point must be inside the warned area"),
) -> dict[str, Any]:
    """Live warnings covering a point (or within `within_km` of it), most serious first."""
    engine = request.app.state.engine
    name = None
    if lat is None or lon is None:
        pid = await place_or_default(request, place)
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT name, lat, lon FROM place WHERE id = :p"), {"p": pid})).first()
        if row is None:
            raise HTTPException(404, f"no place {pid!r}")
        name, lat, lon = row
    sql = (f"SELECT {COLUMNS} {FROM} WHERE w.severity_level <= 3 AND a.geom IS NOT NULL "
           f"AND ST_DWithin(a.geom, {POINT}, :m) ORDER BY w.severity_level, a.label")  # fmt: skip
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"lat": lat, "lon": lon, "m": within_km * 1000.0})).all()
        waiting = (await conn.execute(text(
            "SELECT count(*) FROM flood_warning w JOIN flood_area a ON a.notation = w.area "
            "WHERE w.severity_level <= 3 AND a.geom IS NULL"))).scalar_one()  # fmt: skip
    return {"place": name, "lat": lat, "lon": lon, "count": len(rows),
            "boundaries_missing": waiting,  # warnings whose area has not been fetched yet cannot be tested
            "warnings": [_warning(r) for r in rows]}  # fmt: skip

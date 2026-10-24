"""Bus, tram and ferry disruptions near your places, from the authorities that publish them to BODS."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from app.auth import require_token

router = APIRouter(prefix="/v1/bus-disruptions", tags=["bus"], dependencies=[Depends(require_token)])

COLUMNS = ("id, publisher, summary, description, reason, planned, starts_at, ends_at, lines, operators, stops, "
           "near_place, distance_km, lat, lon, seen")  # fmt: skip


def _row(r: Any) -> dict[str, Any]:
    def iso(x: Any) -> str | None:
        return x.isoformat() if x else None

    return {"id": r.id, "publisher": r.publisher, "summary": r.summary, "description": r.description,
            "reason": r.reason, "planned": r.planned, "starts_at": iso(r.starts_at), "ends_at": iso(r.ends_at),
            "lines": list(r.lines or []), "operators": list(r.operators or []), "stops": len(r.stops or []),
            "near_place": r.near_place, "distance_km": r.distance_km, "lat": r.lat, "lon": r.lon}  # fmt: skip


@router.get("")
async def near(
    request: Request,
    place: str | None = Query(None, description="only those nearest this saved place"),
    line: str | None = Query(None, description="only those affecting this line"),
    within_km: float | None = Query(None, gt=0, le=100),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Current disruptions near your places, closest first. Empty where no authority publishes."""
    where, args = ["(ends_at IS NULL OR ends_at >= now())"], {"limit": limit}
    if place:
        where.append("near_place = :place")
        args["place"] = place
    if line:
        where.append("upper(:line) = ANY(SELECT upper(x) FROM unnest(lines) x)")
        args["line"] = line
    if within_km:
        where.append("distance_km <= :km")
        args["km"] = within_km
    sql = f"SELECT {COLUMNS} FROM bus_disruption WHERE {' AND '.join(where)} ORDER BY distance_km LIMIT :limit"
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(sql), args)).all()
        seen = (await conn.execute(text("SELECT max(seen) FROM bus_disruption"))).scalar_one()
    return {"count": len(rows), "updated": seen.isoformat() if seen else None,
            "disruptions": [_row(r) for r in rows]}  # fmt: skip

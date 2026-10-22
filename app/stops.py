"""Bus stops, stations and terminals, from the NaPTAN register held in Postgres."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.geoquery import distance_km, params, within_sql
from app.integrations.naptan.normalise import KINDS

router = APIRouter(prefix="/v1/stops", tags=["stops"], dependencies=[Depends(require_token)])

KIND_NAMES = sorted(set(KINDS.values()))
COLUMNS = "atco, naptan_code, name, street, indicator, locality, kind, stop_type, lat, lon"


def _stop(r: Any, km: float | None = None) -> dict[str, Any]:
    out = {"id": r.atco, "naptan_code": r.naptan_code, "name": r.name, "street": r.street,
           "indicator": r.indicator, "locality": r.locality, "kind": r.kind, "stop_type": r.stop_type,
           "lat": r.lat, "lon": r.lon}  # fmt: skip
    if km is not None:
        out["km"] = round(float(km), 3)
    return out


@router.get("/near")
async def near(
    request: Request,
    lat: float = Query(..., ge=49.5, le=61.0),
    lon: float = Query(..., ge=-8.7, le=2.0),
    radius_km: float = Query(0.5, gt=0, le=20),
    kind: str | None = Query(None, description=f"one of {', '.join(KIND_NAMES)}"),
    limit: int = Query(25, ge=1, le=200),
) -> dict[str, Any]:
    """Stops within a radius, nearest first."""
    if kind is not None and kind not in KIND_NAMES:
        raise HTTPException(422, f"no kind {kind!r}; choose from {', '.join(KIND_NAMES)}")
    sql = (f"SELECT {COLUMNS}, {distance_km()} AS km FROM naptan_stop WHERE {within_sql()}"
           + (" AND kind = :kind" if kind else "") + " ORDER BY km LIMIT :limit")
    extra: dict[str, Any] = {"limit": limit}
    if kind:
        extra["kind"] = kind
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(sql), params(lat, lon, radius_km, **extra))).all()
    return {"stops": [_stop(r, r.km) for r in rows]}


@router.get("/{atco}")
async def one(request: Request, atco: str) -> dict[str, Any]:
    """One stop by ATCO code, the id TfL and the bus tracker use."""
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text(f"SELECT {COLUMNS} FROM naptan_stop WHERE atco = :a"),
                                  {"a": atco})).first()  # fmt: skip
    if row is None:
        raise HTTPException(404, f"no stop {atco!r}")
    return _stop(row)

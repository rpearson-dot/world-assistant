"""Postcodes from the local Code-Point Open table: look one up, or find the nearest to a point."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth import require_token
from app.geo import Located, PostcodeError, normalise_postcode
from app.geoquery import distance_km, params, within_sql

log = logging.getLogger("wa.postcodes")

router = APIRouter(prefix="/v1/postcodes", tags=["postcodes"], dependencies=[Depends(require_token)])

COLUMNS = "postcode, lat, lon, quality, country, district, ward"


async def local_postcode(engine: AsyncEngine, value: str) -> Located | None:
    """A postcode's position from the local table, or None.

    None means "ask someone else": the table is empty, not built yet, or does not know the postcode
    (Code-Point Open is quarterly, so a new postcode can be missing). A malformed postcode also gives
    None, and the fallback is the one that complains about it.
    """
    try:
        pc = normalise_postcode(value).replace(" ", "")
    except PostcodeError:
        return None
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT postcode, lat, lon FROM postcode WHERE pc = :pc"), {"pc": pc})).first()
    except DBAPIError:
        log.warning("the postcode table is not available; falling back to postcodes.io")
        return None
    return Located(row.postcode, float(row.lat), float(row.lon)) if row else None


def _row(r: Any, km: float | None = None) -> dict[str, Any]:
    out = {"postcode": r.postcode, "lat": r.lat, "lon": r.lon, "quality": r.quality,
           "country": r.country, "district": r.district, "ward": r.ward}  # fmt: skip
    if km is not None:
        out["km"] = round(float(km), 3)
    return out


@router.get("/near")
async def near(
    request: Request,
    lat: float = Query(..., ge=49.5, le=61.0),
    lon: float = Query(..., ge=-8.7, le=2.0),
    radius_km: float = Query(0.2, gt=0, le=5),
    limit: int = Query(5, ge=1, le=50),
) -> dict[str, Any]:
    """The nearest postcodes to a point: a reverse lookup, for a pin dropped on a map."""
    sql = (f"SELECT {COLUMNS}, {distance_km()} AS km FROM postcode WHERE {within_sql()} "
           "ORDER BY km LIMIT :limit")  # fmt: skip
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(sql), params(lat, lon, radius_km, limit=limit))).all()
    return {"postcodes": [_row(r, r.km) for r in rows]}


@router.get("/{postcode}")
async def one(request: Request, postcode: str) -> dict[str, Any]:
    """One postcode's position."""
    try:
        pc = normalise_postcode(postcode).replace(" ", "")
    except PostcodeError as exc:
        raise HTTPException(422, str(exc)) from exc
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text(f"SELECT {COLUMNS} FROM postcode WHERE pc = :pc"), {"pc": pc})).first()
    if row is None:
        raise HTTPException(404, f"{postcode!r} is not in the local table")
    return _row(row)




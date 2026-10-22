"""Live buses near a point, anywhere in Great Britain, from the Bus Open Data Service.

Unlike the bus tracker (one TfL route, recorded all morning), this is a snapshot: where every bus that
reports a position is right now. Nothing is stored. Results are cached for 30 seconds per area, so a map
being refreshed does not become a stream of calls to BODS.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from redis.exceptions import RedisError
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.integrations.bods.client import BodsError, box_around, fetch_vehicles
from app.integrations.fuelfinder.queries import haversine_km
from app.places import place_or_default

log = logging.getLogger("wa.bods")

router = APIRouter(prefix="/v1/live-buses", tags=["live buses"], dependencies=[Depends(require_token)])

CACHE_SECONDS = 10  # BODS refreshes positions every 10 seconds
MAX_RADIUS_KM = 15.0  # a box this size around a city is a few thousand buses; wider is a slow, large reply


async def vehicles_near(
    redis: Any, lat: float, lon: float, radius_km: float, line: str | None = None
) -> list[dict[str, Any]]:
    """Buses within the circle, nearest first. Raises BodsError when the feed cannot be used."""
    radius_km = min(radius_km, MAX_RADIUS_KM)
    key = f"wa:bods:{lat:.2f}:{lon:.2f}:{radius_km:.0f}"
    cached = None
    try:
        cached = await redis.get(key)
    except (OSError, RedisError):  # a cache that is down must not take the feature with it
        log.warning("bus cache unavailable")
    if cached:
        vehicles = json.loads(cached)
    else:
        vehicles = await fetch_vehicles(get_settings().bods_api_key, box_around(lat, lon, radius_km))
        try:
            await redis.set(key, json.dumps(vehicles), ex=CACHE_SECONDS)
        except (OSError, RedisError):
            log.warning("bus cache unavailable")
    for v in vehicles:
        v["km"] = round(haversine_km((lat, lon), (v["lat"], v["lon"])), 3)
    near = [v for v in vehicles if v["km"] <= radius_km and (not line or (v["line"] or "").upper() == line.upper())]
    return sorted(near, key=lambda v: v["km"])


@router.get("")
async def live(
    request: Request,
    place: str | None = Query(None, description="a saved place id; default: home"),
    lat: float | None = Query(None, ge=49.5, le=61.0),
    lon: float | None = Query(None, ge=-8.7, le=2.0),
    radius_km: float = Query(2.0, gt=0, le=MAX_RADIUS_KM),
    line: str | None = Query(None, max_length=20, description="only this line, e.g. 213"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Buses reporting a position near a place or point, nearest first."""
    if lat is None or lon is None:
        pid = await place_or_default(request, place)
        async with request.app.state.engine.connect() as conn:
            row = (await conn.execute(text("SELECT lat, lon FROM place WHERE id = :p"), {"p": pid})).first()
        if row is None:
            raise HTTPException(404, f"no place {pid!r}")
        lat, lon = row
    if not get_settings().bods_api_key:
        raise HTTPException(503, "live buses need a free BODS key: set the bods_api_key secret")
    try:
        vehicles = await vehicles_near(request.app.state.redis, lat, lon, radius_km, line)
    except BodsError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"lat": lat, "lon": lon, "radius_km": radius_km, "count": len(vehicles), "buses": vehicles[:limit]}

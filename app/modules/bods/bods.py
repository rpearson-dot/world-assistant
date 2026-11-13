"""Live buses near a point, anywhere in Great Britain, from the Bus Open Data Service.

Unlike the bus tracker (one TfL route, recorded all morning), this is a snapshot: where every bus that
reports a position is right now. Nothing is stored. Results are cached for 10 seconds per area, so a map
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
from app.geoquery import haversine_km
from app.modules.bods.sources.bods.client import BodsError, box_around, fetch_vehicles
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


@router.get("/departures")
async def departures(
    request: Request,
    lat: float = Query(..., ge=49.5, le=61.0),
    lon: float = Query(..., ge=-8.7, le=2.0),
    radius_m: float = Query(400.0, ge=50, le=1500),
    stops: int = Query(4, ge=1, le=10),
) -> dict[str, Any]:
    """Next buses at the bus stops nearest a point, outside London as well as in it: each bus on its way, minutes
    worked out from where it is along its route (at a typical speed, so not seeing traffic), and failing that the
    timetable's next weekday times. A line whose timetable is still being fetched says so; ask again shortly."""
    from app.geoquery import distance_km, params, within_sql
    from app.modules.bus.sources.bus.departures import departures as next_buses
    from app.modules.naptan.stops import COLUMNS, _stop

    key = get_settings().bods_api_key
    sql = (f"SELECT {COLUMNS}, {distance_km()} AS km FROM naptan_stop WHERE {within_sql()} AND kind = 'bus_stop' "
           "ORDER BY km LIMIT :limit")
    async with request.app.state.engine.connect() as conn:
        near = [_stop(r, r.km) for r in (await conn.execute(text(sql), params(lat, lon, radius_m / 1000, limit=stops))).all()]
    if not near:
        return {"stops": [], "lines": [], "loading": [], "no_timetable": [], "note": f"No bus stops within {radius_m:g} m."}
    london = [s for s in near if str(s["id"]).startswith("490")]  # TfL's own stop ids: its countdowns are exact
    rest = [s for s in near if s not in london]
    out: dict[str, Any] = {"stops": [], "lines": [], "loading": [], "no_timetable": [], "note": ""}
    if london:
        out["stops"] += await _tfl_departures(request, london)
        out["note"] = "London stops: TfL's countdowns. "
    if rest and not key:
        out["note"] += "Outside London needs a BODS key: add a free one under Settings > Keys."
        out["stops"] += [{**s, "buses": []} for s in rest]
    elif rest:
        try:
            vehicles = await vehicles_near(request.app.state.redis, lat, lon, 4.0)
            got = await next_buses(request.app.state.engine, key, lat, lon, rest, vehicles=vehicles)
        except BodsError as exc:
            raise HTTPException(502, str(exc)) from exc
        out["stops"] += got["stops"]
        out.update(lines=got["lines"], loading=got["loading"], no_timetable=got["no_timetable"])
        out["note"] += got["note"]
    order = {s["id"]: i for i, s in enumerate(near)}
    out["stops"].sort(key=lambda s: order.get(s["id"], 99))
    out["note"] = out["note"].strip()
    return out


async def _tfl_departures(request: Request, stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """TfL's arrivals at London stops, in the same shape as the BODS estimate."""
    from app.modules.tfl.sources.tfl.client import TflClient, TflError
    from app.ratelimit import SlidingWindowLimiter

    key = get_settings().tfl_app_key
    out = []
    async with TflClient(key, limiter=SlidingWindowLimiter(request.app.state.redis, "tfl", 250 if key else 30)) as c:
        for s in stops:
            try:
                rows = await c.get(f"/StopPoint/{s['id']}/Arrivals")
            except TflError as exc:
                out.append({**s, "buses": [], "error": str(exc)})
                continue
            buses = sorted(({"line": r.get("lineName") or r.get("lineId"), "towards": r.get("destinationName"),
                             "minutes": max(round((r.get("timeToStation") or 0) / 60), 0), "live": True,
                             "vehicle": r.get("vehicleId")} for r in rows or [] if isinstance(r, dict)),
                           key=lambda b: b["minutes"])  # fmt: skip
            out.append({**s, "buses": buses[:8]})
    return out

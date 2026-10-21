"""Every geospatial dataset as GeoJSON, one layer each, so a map can draw them together.

Each layer is a FeatureCollection whose features carry the fields a map needs for a label or a
colour, and nothing more. Point layers are cut to the radius asked for; line layers (road corridors,
road events) come in whole if any part of them falls inside it, so a closure is never half-drawn.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.integrations.fuelfinder.normalise import canonical_fuel
from app.integrations.fuelfinder.queries import haversine_km
from app.places import place_or_default

router = APIRouter(prefix="/v1/map", tags=["map"], dependencies=[Depends(require_token)])

Point = tuple[float, float]  # (lat, lon), as the rest of the project stores them

LAYERS: dict[str, str] = {
    "places": "Saved places",
    "locations": "Home Assistant locations",
    "roads": "Road corridors watched",
    "road_events": "Closures and speed limits",
    "signs": "Motorway signs",
    "fuel": "Fuel stations",
    "pollen": "Allergy risk lattice",
}
POLLEN_LEVELS = ["none", "low", "moderate", "high", "very high"]


def _feature(geometry: dict[str, Any], props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": props}


def _point(lat: float, lon: float, props: dict[str, Any]) -> dict[str, Any]:
    return _feature({"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}, props)


def _line(points: Sequence[Point], props: dict[str, Any]) -> dict[str, Any]:
    return _feature(
        {"type": "LineString", "coordinates": [[round(lon, 6), round(lat, 6)] for lat, lon in points]},
        props,
    )  # fmt: skip


def _collection(features: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return {"type": "FeatureCollection", "name": name, "features": features}


def within(centre: Point | None, radius_km: float, lat: float | None, lon: float | None) -> bool:
    """A point is in range when there is no centre, or it is inside the radius."""
    if centre is None:
        return True
    if lat is None or lon is None:
        return False
    return haversine_km(centre, (lat, lon)) <= radius_km


def line_within(centre: Point | None, radius_km: float, points: Sequence[Point]) -> bool:
    """A line is in range when any of its points is: a closure drawn in part is worse than none."""
    if centre is None:
        return True
    return any(haversine_km(centre, p) <= radius_km for p in points)


def pollen_level(forecast: Any) -> tuple[str | None, int | None]:
    """The day's allergy risk as (label, 0-4) from a stored forecast, or (None, None)."""
    if isinstance(forecast, dict):
        labels = forecast.get("allergy_risk_label") or forecast.get("label") or []
        first = labels[0] if isinstance(labels, list) and labels else None
        if isinstance(first, str) and first in POLLEN_LEVELS:
            return first, POLLEN_LEVELS.index(first)
    return None, None


async def _places(conn: Any, centre: Point | None, radius_km: float) -> list[dict[str, Any]]:
    res = await conn.execute(text("SELECT id, name, kind, postcode, lat, lon, origin FROM place ORDER BY name"))
    return [_point(r.lat, r.lon, {"id": r.id, "name": r.name, "kind": r.kind, "postcode": r.postcode,
                                  "origin": r.origin})
            for r in res if within(centre, radius_km, r.lat, r.lon)]  # fmt: skip


async def _locations(conn: Any, centre: Point | None, radius_km: float) -> list[dict[str, Any]]:
    res = await conn.execute(
        text("SELECT id, name, lat, lon, radius_km, last_seen FROM ha_location ORDER BY name")
    )
    return [_point(r.lat, r.lon, {"id": r.id, "name": r.name, "radius_km": r.radius_km,
                                  "last_seen": r.last_seen.isoformat() if r.last_seen else None})
            for r in res if within(centre, radius_km, r.lat, r.lon)]  # fmt: skip


async def _roads(conn: Any, centre: Point | None, radius_km: float) -> list[dict[str, Any]]:
    res = await conn.execute(
        text("SELECT id, name, centreline, buffer_m, roads, enabled FROM nh_corridor "
             "WHERE enabled ORDER BY name")
    )  # fmt: skip
    out = []
    for r in res:
        pts = [(float(p[0]), float(p[1])) for p in (r.centreline or []) if len(p) >= 2]
        if pts and line_within(centre, radius_km, pts):
            out.append(_line(pts, {"id": r.id, "name": r.name, "buffer_m": r.buffer_m,
                                   "roads": list(r.roads or [])}))  # fmt: skip
    return out


async def _road_events(conn: Any, centre: Point | None, radius_km: float) -> list[dict[str, Any]]:
    res = await conn.execute(
        text("SELECT record_id, kind, status, cause, detail, limit_mph, roads, segments, start_time, end_time "
             "FROM nh_event WHERE cleared_at IS NULL ORDER BY start_time DESC NULLS LAST")
    )  # fmt: skip
    out = []
    for r in res:
        for i, seg in enumerate(r.segments or []):
            pts = [(float(p[0]), float(p[1])) for p in (seg.get("points") or []) if len(p) >= 2]
            if not pts or not line_within(centre, radius_km, pts):
                continue
            props = {"id": f"{r.record_id}#{i}", "kind": r.kind, "status": r.status, "cause": r.cause,
                     "detail": r.detail, "limit_mph": r.limit_mph, "road": seg.get("road"),
                     "direction": seg.get("direction"), "description": seg.get("description"),
                     "start": r.start_time.isoformat() if r.start_time else None,
                     "end": r.end_time.isoformat() if r.end_time else None}  # fmt: skip
            out.append(_line(pts, props) if len(pts) > 1 else _point(pts[0][0], pts[0][1], props))
    return out


async def _signs(conn: Any, centre: Point | None, radius_km: float) -> list[dict[str, Any]]:
    res = await conn.execute(
        text("SELECT sign_id, road, direction, description, lat, lon, working, lines, time_last_set "
             "FROM nh_sign WHERE lat IS NOT NULL AND lon IS NOT NULL ORDER BY road, direction")
    )  # fmt: skip
    return [_point(r.lat, r.lon, {"id": r.sign_id, "road": r.road, "direction": r.direction,
                                  "description": r.description, "working": r.working,
                                  "lines": list(r.lines or []),
                                  "set_at": r.time_last_set.isoformat() if r.time_last_set else None})
            for r in res if within(centre, radius_km, r.lat, r.lon)]  # fmt: skip


async def _fuel(conn: Any, centre: Point | None, radius_km: float, fuel: str) -> list[dict[str, Any]]:
    res = await conn.execute(
        text("SELECT s.node_id, s.brand_name, s.trading_name, s.postcode, s.lat, s.lon, s.is_motorway, "
             "s.is_supermarket, p.pence, p.last_updated "
             "FROM fuel_station s LEFT JOIN fuel_price p ON p.node_id = s.node_id AND p.fuel_type = :f "
             "WHERE s.coords_ok AND NOT s.permanent_closure"),
        {"f": fuel},
    )  # fmt: skip
    return [_point(r.lat, r.lon, {"id": r.node_id, "brand": r.brand_name, "name": r.trading_name,
                                  "postcode": r.postcode, "motorway": r.is_motorway,
                                  "supermarket": r.is_supermarket, "fuel": fuel, "pence": r.pence,
                                  "price_at": r.last_updated.isoformat() if r.last_updated else None})
            for r in res if within(centre, radius_km, r.lat, r.lon)]  # fmt: skip


async def _pollen(conn: Any, centre: Point | None, radius_km: float) -> list[dict[str, Any]]:
    res = await conn.execute(text("SELECT lat, lon, forecast, changed_at FROM pollen_grid"))
    out = []
    for r in res:
        if not within(centre, radius_km, r.lat, r.lon):
            continue
        label, level = pollen_level(r.forecast)
        out.append(_point(r.lat, r.lon, {"label": label, "level": level,
                                         "changed_at": r.changed_at.isoformat() if r.changed_at else None}))  # fmt: skip
    return out


@router.get("/layers")
async def layers(request: Request) -> dict[str, Any]:
    """What can be drawn, and how much of it there is."""
    counts = {
        "places": "SELECT count(*) FROM place",
        "locations": "SELECT count(*) FROM ha_location",
        "roads": "SELECT count(*) FROM nh_corridor WHERE enabled",
        "road_events": "SELECT count(*) FROM nh_event WHERE cleared_at IS NULL",
        "signs": "SELECT count(*) FROM nh_sign WHERE lat IS NOT NULL",
        "fuel": "SELECT count(*) FROM fuel_station WHERE coords_ok AND NOT permanent_closure",
        "pollen": "SELECT count(*) FROM pollen_grid",
    }
    out = []
    async with request.app.state.engine.connect() as conn:
        for key, name in LAYERS.items():
            n = (await conn.execute(text(counts[key]))).scalar_one()
            out.append({"id": key, "name": name, "features": n})
    return {"layers": out}


@router.get("/geojson")
async def geojson(
    request: Request,
    layers: str = Query("places,roads,road_events,fuel", description="comma-separated layer ids"),
    place: str | None = Query(None, description="id of a saved place to centre on; default: home"),
    lat: float | None = Query(None, ge=49.5, le=61.0),
    lon: float | None = Query(None, ge=-8.7, le=2.0),
    radius_km: float = Query(10.0, gt=0, le=500),
    everywhere: bool = Query(False, description="no centre: the whole country, every feature"),
    fuel: str = Query("E10", description="which fuel's price to carry on the station features"),
) -> dict[str, Any]:
    wanted = [x.strip() for x in layers.split(",") if x.strip()]
    unknown = [x for x in wanted if x not in LAYERS]
    if unknown:
        raise HTTPException(422, f"no layer {unknown[0]!r}; choose from {', '.join(LAYERS)}")
    fuel_key = canonical_fuel(fuel)
    if fuel_key is None:
        raise HTTPException(422, f"no fuel {fuel!r}")

    engine = request.app.state.engine
    centre: Point | None = None
    centre_name: str | None = None
    if not everywhere:
        if lat is not None and lon is not None:
            centre = (lat, lon)
        else:
            pid = await place_or_default(request, place)
            async with engine.connect() as conn:
                row = (await conn.execute(
                    text("SELECT name, lat, lon FROM place WHERE id = :p"), {"p": pid})).first()  # fmt: skip
            if row is None:
                raise HTTPException(404, f"no place {pid!r}")
            centre_name, centre = row[0], (row[1], row[2])

    out: dict[str, Any] = {}
    async with engine.connect() as conn:
        for key in wanted:
            if key == "places":
                features = await _places(conn, centre, radius_km)
            elif key == "locations":
                features = await _locations(conn, centre, radius_km)
            elif key == "roads":
                features = await _roads(conn, centre, radius_km)
            elif key == "road_events":
                features = await _road_events(conn, centre, radius_km)
            elif key == "signs":
                features = await _signs(conn, centre, radius_km)
            elif key == "fuel":
                features = await _fuel(conn, centre, radius_km, fuel_key)
            else:
                features = await _pollen(conn, centre, radius_km)
            out[key] = _collection(features, LAYERS[key])
    where = None
    if centre is not None:
        where = {"name": centre_name, "lat": centre[0], "lon": centre[1], "radius_km": radius_km}
    return {"centre": where, "layers": out}

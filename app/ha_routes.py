"""Routes added from Home Assistant: two positions, saved as places and a trip, then routed locally.

Home Assistant sends two named positions (usually two of its zones). This saves them as places (ids
start `ha-` so they never touch places made on the web pages, and the daily pollen history ignores them),
creates a trip in each direction, and builds the route with the routing engine. The trip's corridor then
shows up as a roads sensor over MQTT. Routing is local, so a route costs no outside requests; the number
of trips is still capped.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings

router = APIRouter(prefix="/v1/ha/routes", tags=["home-assistant"], dependencies=[Depends(require_token)])
ORIGIN = "home-assistant"


class Position(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    lat: float = Field(ge=49.5, le=61.0)
    lon: float = Field(ge=-8.7, le=2.0)


class RouteIn(BaseModel):
    from_name: str = Field(min_length=1, max_length=60)
    from_lat: float = Field(ge=49.5, le=61.0)
    from_lon: float = Field(ge=-8.7, le=2.0)
    to_name: str = Field(min_length=1, max_length=60)
    to_lat: float = Field(ge=49.5, le=61.0)
    to_lon: float = Field(ge=-8.7, le=2.0)
    both_ways: bool = True

    @model_validator(mode="after")
    def _distinct(self) -> RouteIn:
        if place_id(self.from_name) == place_id(self.to_name):
            raise ValueError("the two ends need different names")
        return self


class EndsIn(BaseModel):
    from_name: str = Field(min_length=1, max_length=60)
    to_name: str = Field(min_length=1, max_length=60)
    both_ways: bool = True


def place_id(name: str) -> str:
    """`ha-` plus the name in lower case with hyphens, at most 16 characters of it."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:16].strip("-") or "x"
    return f"ha-{slug}"


def trip_id(a: str, b: str) -> str:
    return f"{a}-to-{b[3:]}"  # both already start "ha-"; at most 3+16+4+16 = 39 characters, within the 41 a place or trip id may have


async def _count(request: Request) -> int:
    async with request.app.state.engine.connect() as conn:
        return int((await conn.execute(text("SELECT count(*) FROM trip WHERE origin = :o"), {"o": ORIGIN})).scalar_one())


@router.get("")
async def list_routes(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        rows = [dict(r._mapping) for r in await conn.execute(text(
            "SELECT id, name, from_place, to_place, distance_km, duration_s, roads, route_built_at "
            "FROM trip WHERE origin = :o ORDER BY name"), {"o": ORIGIN})]  # fmt: skip
    return {"routes": rows, "max": get_settings().ha_routes_max}


@router.post("")
async def add_route(body: RouteIn, request: Request) -> dict[str, Any]:
    from sqlalchemy.exc import IntegrityError

    from app.routing import RoutingError, ValhallaClient, build_trips

    a, b = place_id(body.from_name), place_id(body.to_name)
    pairs = [(a, b)] + ([(b, a)] if body.both_ways else [])
    ids = [trip_id(x, y) for x, y in pairs]
    async with request.app.state.engine.connect() as conn:
        have = {r[0] for r in await conn.execute(text("SELECT id FROM trip WHERE id = ANY(:i)"), {"i": ids})}
    maximum = get_settings().ha_routes_max
    if await _count(request) + len([i for i in ids if i not in have]) > maximum:
        return {"ok": False, "message": f"That would go over the limit of {maximum} routes (each direction counts "
                                        "as one). Remove a route first."}  # fmt: skip
    try:
        async with request.app.state.engine.begin() as conn:
            for pid, name, lat, lon in ((a, body.from_name, body.from_lat, body.from_lon),
                                        (b, body.to_name, body.to_lat, body.to_lon)):  # fmt: skip
                await conn.execute(text(
                    """INSERT INTO place (id, name, kind, lat, lon, origin) VALUES (:id, :n, 'other', :lat, :lon, :o)
                       ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, lat = EXCLUDED.lat,
                         lon = EXCLUDED.lon, updated_at = now()"""),
                    {"id": pid, "n": name, "lat": lat, "lon": lon, "o": ORIGIN})  # fmt: skip
            for (x, y), tid in zip(pairs, ids, strict=True):
                nx = body.from_name if x == a else body.to_name
                ny = body.to_name if y == b else body.from_name
                await conn.execute(text(
                    """INSERT INTO trip (id, name, from_place, to_place, enabled, origin)
                       VALUES (:id, :n, :f, :t, true, :o)
                       ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, enabled = true"""),
                    {"id": tid, "n": f"{nx} to {ny}", "f": x, "t": y, "o": ORIGIN})  # fmt: skip
    except IntegrityError:
        return {"ok": False, "message": "Could not save the route."}
    client = ValhallaClient(get_settings().valhalla_url)
    built: list[dict[str, Any]] = []
    error = ""
    try:
        built = await build_trips(request.app.state.engine, client, ids)
    except RoutingError as exc:
        error = str(exc)
    finally:
        await client.aclose()
    if error:
        return {"ok": True, "built": False, "message": f"Saved {body.from_name} to {body.to_name}, but the route "
                f"could not be built yet ({error}). Add it again once the routing engine is running."}  # fmt: skip
    first = built[0]
    return {"ok": True, "built": True, "routes": built,
            "message": f"{body.from_name} to {body.to_name}: {first['distance_km']} km, about {first['duration_min']} min "
                       f"via {', '.join(first['roads'][:4]) or 'local roads'}. It appears as a roads sensor within five "
                       "minutes."}  # fmt: skip


@router.post("/delete")
async def delete_route(body: EndsIn, request: Request) -> dict[str, Any]:
    a, b = place_id(body.from_name), place_id(body.to_name)
    ids = [trip_id(a, b)] + ([trip_id(b, a)] if body.both_ways else [])
    async with request.app.state.engine.begin() as conn:
        corridors = [r[0] for r in await conn.execute(text(
            "SELECT corridor_id FROM trip WHERE origin = :o AND id = ANY(:i) AND corridor_id IS NOT NULL"),
            {"o": ORIGIN, "i": ids})]  # fmt: skip
        res = await conn.execute(text("DELETE FROM trip WHERE origin = :o AND id = ANY(:i)"), {"o": ORIGIN, "i": ids})
        if corridors:  # the roads sensor for the route goes with it
            await conn.execute(text("DELETE FROM nh_corridor WHERE id = ANY(:c) AND source = 'route'"), {"c": corridors})
        await conn.execute(text(  # drop places that nothing uses any more
            """DELETE FROM place WHERE origin = :o AND id NOT IN (SELECT from_place FROM trip UNION SELECT to_place FROM trip)"""),
            {"o": ORIGIN})  # fmt: skip
    if not res.rowcount:
        return {"ok": False, "message": "No such route."}
    return {"ok": True, "message": f"Removed {body.from_name} to {body.to_name}."}

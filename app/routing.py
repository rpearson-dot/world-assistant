"""Turn a trip (two places) into a route and a National Highways corridor.

Valhalla gives the road line, a base driving time (no live traffic) and the road
numbers used. The line is simplified and stored as the corridor, then stored
events are re-tagged against it.

    python -m app.routing build [trip-id ...]     # all enabled trips if none given
"""

from __future__ import annotations

import argparse
import asyncio
import math
import re
import sys
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.nationalhighways.match import Corridor
from app.integrations.nationalhighways.store import rematch_all, upsert_corridor

Point = tuple[float, float]
_ROAD = re.compile(r"^([AM])(\d{1,4})(\(M\))?$")
EARTH_M = 6371008.8


class RoutingError(Exception):
    pass


@dataclass(frozen=True)
class Route:
    points: tuple[Point, ...]
    distance_km: float
    duration_s: int
    roads: tuple[str, ...]  # strategic road numbers in the order first used


def decode_polyline(encoded: str, precision: int = 6) -> list[Point]:
    """Google-style encoded polyline (Valhalla uses precision 6) -> (lat, lon) points."""
    factor = 10**precision
    points: list[Point] = []
    index = lat = lon = 0
    while index < len(encoded):
        for axis in (0, 1):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        points.append((lat / factor, lon / factor))
    return points


def _xy(p: Point, lat0: float) -> tuple[float, float]:
    return (
        math.radians(p[1]) * math.cos(math.radians(lat0)) * EARTH_M,
        math.radians(p[0]) * EARTH_M,
    )


def simplify(points: list[Point], tolerance_m: float = 30.0) -> list[Point]:
    """Douglas-Peucker line simplification (iterative, so long routes are safe)."""
    if len(points) < 3:
        return list(points)
    lat0 = sum(p[0] for p in points) / len(points)
    xy = [_xy(p, lat0) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        worst, worst_d = -1, 0.0
        for i in range(a + 1, b):
            px, py = xy[i]
            if length2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > worst_d:
                worst, worst_d = i, d
        if worst_d > tolerance_m and worst >= 0:
            keep[worst] = True
            stack += [(a, worst), (worst, b)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def road_numbers(maneuvers: list[dict[str, Any]]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for m in maneuvers:
        for name in m.get("street_names") or []:
            if _ROAD.match(name.strip()):
                seen.setdefault(name.strip(), None)
    return tuple(seen)


def parse_route(body: dict[str, Any]) -> Route:
    try:
        trip = body["trip"]
        leg_points: list[Point] = []
        maneuvers: list[dict[str, Any]] = []
        for leg in trip["legs"]:
            pts = decode_polyline(leg["shape"])
            leg_points += pts[1:] if leg_points else pts
            maneuvers += leg.get("maneuvers", [])
        summary = trip["summary"]
        return Route(
            tuple(leg_points), float(summary["length"]), round(float(summary["time"])),
            road_numbers(maneuvers),
        )  # fmt: skip
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise RoutingError(f"unexpected routing response: {exc!r}") from exc


class ValhallaClient:
    def __init__(
        self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:  # fmt: skip
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def route(self, start: Point, end: Point) -> Route:
        payload = {
            "locations": [{"lat": start[0], "lon": start[1]}, {"lat": end[0], "lon": end[1]}],
            "costing": "auto",
            "directions_options": {"units": "kilometers"},
        }
        try:
            r = await self._http.post("/route", json=payload)
        except httpx.HTTPError as exc:
            raise RoutingError(f"routing engine unreachable: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise RoutingError(f"routing engine returned HTTP {r.status_code}: {r.text[:200]}")
        return parse_route(r.json())


def corridor_for(trip_id: str, name: str, route: Route, buffer_m: float = 500.0) -> Corridor:
    return Corridor(
        id=f"trip-{trip_id}",
        name=name,
        centreline=tuple(simplify(list(route.points))),
        buffer_m=buffer_m,
        roads=frozenset(route.roads),
    )


async def build_trip(engine: AsyncEngine, client: ValhallaClient, trip_id: str) -> dict[str, Any]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT t.id, t.name, f.lat AS flat, f.lon AS flon, d.lat AS tlat, d.lon AS tlon
                    FROM trip t JOIN place f ON f.id = t.from_place JOIN place d ON d.id = t.to_place
                    WHERE t.id = :id
                    """
                ),
                {"id": trip_id},
            )
        ).mappings().first()
    if row is None:
        raise RoutingError(f"no trip {trip_id!r}")
    route = await client.route((row["flat"], row["flon"]), (row["tlat"], row["tlon"]))
    corridor = corridor_for(row["id"], row["name"], route)
    await upsert_corridor(engine, corridor, source="route")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE trip SET corridor_id = :c, distance_km = :km, duration_s = :s,
                       roads = :roads, route_built_at = now() WHERE id = :id
                """
            ),
            {"c": corridor.id, "km": route.distance_km, "s": route.duration_s,
             "roads": list(route.roads), "id": trip_id},
        )  # fmt: skip
    return {"trip": trip_id, "corridor": corridor.id, "distance_km": round(route.distance_km, 1),
            "duration_min": round(route.duration_s / 60), "roads": list(route.roads),
            "points": len(corridor.centreline)}  # fmt: skip


async def build_trips(engine: AsyncEngine, client: ValhallaClient, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        async with engine.connect() as conn:
            ids = [r[0] for r in await conn.execute(text("SELECT id FROM trip WHERE enabled ORDER BY id"))]
    out = [await build_trip(engine, client, i) for i in ids]
    await rematch_all(engine)
    return out


async def _cli(ids: list[str]) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    client = ValhallaClient(settings.valhalla_url)
    try:
        for item in await build_trips(engine, client, ids):
            print(item)
        return 0
    except RoutingError as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await client.aclose()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build routes and corridors for trips.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="route trips and (re)build their corridors")
    build.add_argument("trips", nargs="*")
    args = parser.parse_args(argv)
    return asyncio.run(_cli(args.trips))


if __name__ == "__main__":
    sys.exit(main())

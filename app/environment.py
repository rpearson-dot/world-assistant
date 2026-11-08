"""Air and water near your places: air quality (Defra's 1-10 index), river levels, rainfall and tide gauges.

    GET /v1/environment/air?place=&km=      the nearest readings of each pollutant, and the index
    GET /v1/environment/rivers?place=&km=   river level gauges: level, usual range, trend
    GET /v1/environment/rain?place=&km=     rain gauges: the last hour and the last 24 hours
    GET /v1/environment/tides?place=&km=    the nearest tide gauge: measured level and trend
    GET /v1/environment/overflows?place=&km=&rivers=   storm overflows nearby and on watched rivers: spilling now,
                                            in the last 48 hours, offline
    GET|PUT /v1/environment/overflows/rivers           the rivers to watch (saved), e.g. ["Hogsmill", "Thames"]
    GET /v1/environment/summary             all of it for home, the index for every place, tides where there are
                                            (Home Assistant's "Air and water" sensors come from this)

Read live from UK-AIR and the Environment Agency and kept ten minutes (app/integrations/environment/clients.py).
The EA's data covers England only.

    docker compose exec api python -m app.environment [place]     what each source sends, to check it works
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.integrations.environment import clients as C
from app.places import place_or_default

router = APIRouter(prefix="/v1/environment", tags=["environment"], dependencies=[Depends(require_token)])
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


LAT = Query(None, ge=49.5, le=61.0, description="with lon: this point, not a place")
LON = Query(None, ge=-8.7, le=2.0)


async def _where(request: Request, place: str | None, lat: float | None = None,
                 lon: float | None = None) -> tuple[str, str, float, float]:  # fmt: skip
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return "here", "here", float(lat), float(lon)
    pid = await place_or_default(request, place)
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text("SELECT name, lat, lon FROM place WHERE id = :p"), {"p": pid})).first()
    if row is None:
        raise HTTPException(404, f"no place {pid!r}")
    return pid, str(row[0]), float(row[1]), float(row[2])


async def _call(fn: Any, *args: Any, **kw: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            return await fn(c, *args, **kw)  # type: ignore[no-any-return]
    except C.EnvError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/air")
async def air(request: Request, place: str | None = Query(None, description="saved place; default: home"),
              km: float = Query(20, gt=0, le=60), lat: float | None = LAT, lon: float | None = LON) -> dict[str, Any]:  # fmt: skip
    """Air quality: the nearest recent reading of NO2, ozone, PM2.5 and PM10, and Defra's Daily Air Quality Index
    (1-3 low, 4-6 moderate, 7-9 high, 10 very high): the highest of the four."""
    pid, name, lat, lon = await _where(request, place, lat, lon)
    return {"place": pid, "name": name, **await _call(C.air_near, lat, lon, km)}


@router.get("/rivers")
async def rivers(request: Request, place: str | None = Query(None, description="saved place; default: home"),
                 km: float = Query(5, gt=0, le=30), lat: float | None = LAT, lon: float | None = LON) -> dict[str, Any]:  # fmt: skip
    """River level gauges near a place: the level now, its usual range there (low, normal, high) and the trend."""
    pid, name, lat, lon = await _where(request, place, lat, lon)
    return {"place": pid, "name": name, **await _call(C.rivers_near, lat, lon, km)}


@router.get("/rain")
async def rain(request: Request, place: str | None = Query(None, description="saved place; default: home"),
               km: float = Query(10, gt=0, le=30), lat: float | None = LAT, lon: float | None = LON) -> dict[str, Any]:  # fmt: skip
    """Rain gauges near a place: millimetres in the last hour and the last 24 hours."""
    pid, name, lat, lon = await _where(request, place, lat, lon)
    return {"place": pid, "name": name, **await _call(C.rain_near, lat, lon, km)}


@router.get("/tides")
async def tides(request: Request, place: str | None = Query(None, description="saved place; default: home"),
                km: float = Query(20, gt=0, le=60), lat: float | None = LAT, lon: float | None = LON) -> dict[str, Any]:  # fmt: skip
    """The nearest tide gauge: the measured level and whether it is rising or falling. Not a prediction."""
    pid, name, lat, lon = await _where(request, place, lat, lon)
    return {"place": pid, "name": name, **await _call(C.tides_near, lat, lon, km)}


RIVERS_KEY = "overflow_rivers"


async def watched_rivers(request: Request) -> list[str]:
    """The rivers whose storm overflows to watch, as saved (none until set)."""
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": RIVERS_KEY})).first()
    v = row[0] if row else None
    if isinstance(v, str):
        v = json.loads(v)
    return [str(r) for r in (v or []) if str(r).strip()]


class Rivers(BaseModel):
    rivers: list[str] = Field(default_factory=list, max_length=20, description="names as the water company writes "
                              "them; any part matches, so 'Hogsmill' finds 'Hogsmill River'")  # fmt: skip


@router.get("/overflows/rivers")
async def get_rivers(request: Request) -> dict[str, Any]:
    """The rivers whose storm overflows are watched, upstream as well as near home (within 15 km)."""
    return {"rivers": await watched_rivers(request)}


@router.put("/overflows/rivers")
async def put_rivers(request: Request, body: Rivers) -> dict[str, Any]:
    """Set the rivers to watch, e.g. {"rivers": ["Hogsmill", "Thames"]}. An empty list watches only what is near."""
    rivers = [r.strip() for r in body.rivers if r.strip()]
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text("INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                           {"k": RIVERS_KEY, "v": json.dumps(rivers)})  # fmt: skip
    return {"rivers": rivers}


@router.get("/overflows")
async def overflows(request: Request, place: str | None = Query(None, description="saved place; default: home"),
                    km: float = Query(3, gt=0, le=20),
                    rivers: str | None = Query(None, description="comma-separated; default: the watched rivers"),
                    river_km: float = Query(15, gt=0, le=60), lat: float | None = LAT,
                    lon: float | None = LON) -> dict[str, Any]:  # fmt: skip
    """Storm overflows (Thames Water's, from the National Storm Overflow Hub) within `km`, and those on the watched
    rivers within `river_km`: which are discharging now, which have in the last 48 hours, and which are offline."""
    pid, name, lat, lon = await _where(request, place, lat, lon)
    watch = [r for r in rivers.split(",")] if rivers is not None else await watched_rivers(request)
    return {"place": pid, "name": name, **await _call(C.overflows_near, lat, lon, km, watch, river_km)}


@router.get("/summary")
async def summary(request: Request) -> dict[str, Any]:
    """Home's air, rivers and rain; the air index for every place; a tide gauge for places within 10 km of one.
    Each part that fails says so in its own `error` and the rest still come back."""
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text("SELECT id, name, kind, lat, lon FROM place ORDER BY id"))).all()
    home_id = await place_or_default(request, None)
    out: dict[str, Any] = {"home": home_id, "places": []}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        async def part(fn: Any, *a: Any, **kw: Any) -> dict[str, Any]:
            try:
                return await fn(c, *a, **kw)  # type: ignore[no-any-return]
            except C.EnvError as exc:
                return {"error": str(exc)}

        for pid, name, _kind, lat, lon in rows:
            p: dict[str, Any] = {"place": pid, "name": name, "air": await part(C.air_near, lat, lon)}
            tide = await part(C.tides_near, lat, lon, 10)
            if tide.get("gauge"):
                p["tide"] = tide
            if pid == home_id:
                p["rivers"] = await part(C.rivers_near, lat, lon)
                p["rain"] = await part(C.rain_near, lat, lon)
                p["overflows"] = await part(C.overflows_near, lat, lon, 3, await watched_rivers(request))
            out["places"].append(p)
    return out


async def probe(lat: float, lon: float) -> None:
    """Print what each source sends near a point, raw and as read here."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for title, fn, kw in (("Air quality (UK-AIR)", C.air_near, {}), ("Rivers (EA)", C.rivers_near, {}),
                              ("Rain (EA)", C.rain_near, {}), ("Tides (EA, 30 km)", C.tides_near, {"km": 30}),
                              ("Storm overflows (Thames Water via the Hub, 3 km)", C.overflows_near, {})):  # fmt: skip
            print(f"\n== {title}")
            try:
                print(json.dumps(await fn(c, lat, lon, **kw), indent=2, default=str)[:4000])
            except C.EnvError as exc:
                print(f"FAILED: {exc}")
        near = '{"center":{"type":"Point","coordinates":[%s,%s]},"radius":20}' % (lon, lat)  # noqa: UP031
        print("\n== UK-AIR stations, raw (first two, by its nearby search)")
        try:
            print(json.dumps((await C._json(c, f"{C.UKAIR}/stations", {"near": near}))[:2], indent=2)[:2000])
        except (C.EnvError, TypeError) as exc:  # not used since 0.38.1 (it fails): only noted, not a failure
            print(f"Note: UK-AIR's nearby search still fails ({exc}); World Assistant reads its full station list instead")
        print("\n== Thames Water overflows, raw (first one within 3 km)")
        try:
            raw = await C._json(c, C.OVERFLOW_FEEDS["Thames Water"], {
                "where": "1=1", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
                "distance": "3000", "units": "esriSRUnit_Meter", "outFields": "*", "returnGeometry": "false",
                "f": "json", "resultRecordCount": "1"})  # fmt: skip
            print(json.dumps(raw, indent=2)[:2000])
        except C.EnvError as exc:
            print(f"FAILED: {exc}")


def main() -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import get_settings

    async def run() -> int:
        place = sys.argv[1] if len(sys.argv) > 1 else None
        engine = create_async_engine(get_settings().database_url)
        async with engine.connect() as conn:
            q = "SELECT lat, lon FROM place WHERE id = :p" if place else \
                "SELECT lat, lon FROM place ORDER BY (kind = 'home') DESC, id LIMIT 1"  # fmt: skip
            row = (await conn.execute(text(q), {"p": place} if place else {})).first()
        await engine.dispose()
        if row is None:
            print("No such place (or no places yet).")
            return 1
        await probe(float(row[0]), float(row[1]))
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())

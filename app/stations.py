"""TfL stations: find one, see its layout and facilities, and what is closed there now (lifts above all)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.auth import require_token
from app.places import place_or_default

router = APIRouter(prefix="/v1/stations", tags=["stations"], dependencies=[Depends(require_token)])

COLUMNS = "id, hub, name, modes, lines, zone, step_free, facilities, entrances, platforms, lat, lon"
ALERT_COLUMNS = "id, station_id, name, kind, description, starts_at, ends_at"


def _iso(x: Any) -> str | None:
    return x.isoformat() if x else None


def station_row(r: Any, full: bool = False) -> dict[str, Any]:
    out = {"id": r.id, "hub": r.hub, "name": r.name, "modes": list(r.modes or []), "lines": list(r.lines or []),
           "zone": r.zone, "step_free": r.step_free, "entrances": r.entrances, "platforms": r.platforms,
           "lat": r.lat, "lon": r.lon}  # fmt: skip
    f = r.facilities or {}
    if full:
        out["facilities"] = f
    else:  # the few people look for
        fac = f.get("Facility", {})
        out["has"] = {k: v for k, v in fac.items() if k in ("Lifts", "Toilets", "Car park", "Escalators", "WiFi")}
    return out


def alert_row(r: Any) -> dict[str, Any]:
    return {"id": r.id, "station_id": r.station_id, "station": r.name, "kind": r.kind, "description": r.description,
            "from": _iso(r.starts_at), "until": _iso(r.ends_at)}  # fmt: skip


async def related_ids(conn: AsyncConnection, ids: list[str]) -> list[str]:
    """The stations given, and the others in the same building (hub): Wimbledon's District, tram and rail."""
    if not ids:
        return []
    rows = await conn.execute(text(
        "SELECT id FROM tfl_station WHERE id = ANY(:ids) OR hub = ANY(:ids) "
        "OR hub IN (SELECT hub FROM tfl_station WHERE id = ANY(:ids) AND hub IS NOT NULL)"), {"ids": ids})  # fmt: skip
    return sorted({*ids, *(r[0] for r in rows)})


async def alerts_at(conn: AsyncConnection, ids: list[str], lifts_only: bool = False) -> list[dict[str, Any]]:
    every = await related_ids(conn, ids)
    sql = f"SELECT {ALERT_COLUMNS} FROM tfl_station_alert WHERE station_id = ANY(:ids)"
    if lifts_only:
        sql += " AND kind IN ('lift', 'step_free')"
    rows = await conn.execute(text(sql + " ORDER BY (kind = 'lift') DESC, name"), {"ids": every})
    return [alert_row(r) for r in rows]


@router.get("")
async def search(request: Request, q: str = Query(min_length=2, max_length=60), limit: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    """Stations whose name contains q, with how many closures each has now."""
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            f"SELECT {COLUMNS} FROM tfl_station WHERE name ILIKE :q ORDER BY (lower(name) = lower(:exact)) DESC, "
            "length(name), name LIMIT :limit"), {"q": f"%{q}%", "exact": q, "limit": limit})).all()  # fmt: skip
        out = []
        for r in rows:
            row = station_row(r)
            row["closures"] = len(await alerts_at(conn, [r.id]))
            out.append(row)
        held = (await conn.execute(text("SELECT count(*) FROM tfl_station"))).scalar_one()
    return {"count": len(out), "stations": out, "loaded": held}


@router.get("/near")
async def near(request: Request, place: str | None = None, km: float = Query(2.0, gt=0, le=20),
               limit: int = Query(10, ge=1, le=50)) -> dict[str, Any]:  # fmt: skip
    """The stations closest to a saved place (home by default)."""
    pid = await place_or_default(request, place)
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            f"SELECT {', '.join('s.' + c for c in COLUMNS.split(', '))}, ST_Distance(s.geog, p.geog) / 1000.0 AS km "
            "FROM tfl_station s JOIN place p ON p.id = :p WHERE ST_DWithin(s.geog, p.geog, :m) "
            "ORDER BY km LIMIT :limit"), {"p": pid, "m": km * 1000, "limit": limit})).all()  # fmt: skip
        out = [{**station_row(r), "km": round(r.km, 2), "closures": await alerts_at(conn, [r.id])} for r in rows]
    return {"place": pid, "count": len(out), "stations": out}


@router.get("/alerts")
async def alerts(request: Request, station: str | None = Query(None, description="ids, comma-separated; none = all"),
                 lifts_only: bool = False) -> dict[str, Any]:  # fmt: skip
    """What is closed now: at the stations given (and others in the same building), or everywhere."""
    async with request.app.state.engine.connect() as conn:
        covered: list[str] | None = None
        if station:
            ids = [s.strip() for s in station.split(",") if s.strip()][:40]
            items = await alerts_at(conn, ids, lifts_only)
            known = await conn.execute(text("SELECT id, hub FROM tfl_station WHERE id = ANY(:ids) OR hub = ANY(:ids)"),
                                       {"ids": ids})  # fmt: skip
            hit = {x for r in known for x in r if x}
            covered = [i for i in ids if i in hit]
        else:
            where = " WHERE kind IN ('lift', 'step_free')" if lifts_only else ""
            items = [alert_row(r) for r in await conn.execute(text(
                f"SELECT {ALERT_COLUMNS} FROM tfl_station_alert{where} ORDER BY name LIMIT 500"))]  # fmt: skip
        seen = (await conn.execute(text("SELECT max(seen) FROM tfl_station_alert"))).scalar_one()
        held = (await conn.execute(text("SELECT count(*) FROM tfl_station"))).scalar_one()
    out = {"count": len(items), "updated": _iso(seen), "stations_loaded": held, "alerts": items}
    if covered is not None:
        out["covered"] = covered  # of the stations asked about, those TfL's data has (the rest: no lift data)
    return out


@router.get("/{station_id}")
async def one(station_id: str, request: Request) -> dict[str, Any]:
    """A station in full: facilities, entrances, platforms, and closures now."""
    async with request.app.state.engine.connect() as conn:
        r = (await conn.execute(text(f"SELECT {COLUMNS} FROM tfl_station WHERE id = :id OR hub = :id "
                                     "ORDER BY (id = :id) DESC LIMIT 1"), {"id": station_id})).first()  # fmt: skip
        if r is None:
            raise HTTPException(404, f"no TfL station {station_id!r} (TfL covers Underground, Overground, DLR, "
                                     "Elizabeth line and tram stations)")  # fmt: skip
        parts = (await conn.execute(text(
            "SELECT id, kind, name, lines, towards, lat, lon FROM tfl_station_part WHERE station_id = :id "
            "ORDER BY kind, name"), {"id": r.id})).all()  # fmt: skip
        out = station_row(r, full=True)
        out["entrance_list"] = [{"id": p.id, "name": p.name, "lat": p.lat, "lon": p.lon} for p in parts if p.kind == "entrance"]
        out["platform_list"] = [{"id": p.id, "name": p.name, "lines": list(p.lines or []), "towards": p.towards}
                                for p in parts if p.kind == "platform"]  # fmt: skip
        out["closures"] = await alerts_at(conn, [r.id])
        out["same_building"] = [x for x in await related_ids(conn, [r.id]) if x != r.id]
    return out

"""EV chargers: sites near a place or along a trip, how many chargers work and are free, by connector, and prices.

GRIDSERVE's network for now (app.integrations.gridserve); the tables keep an `operator` so others can join.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.conditions import ROUTE, TRIP_SQL
from app.config import get_settings
from app.integrations.gridserve import normalise as N
from app.places import place_or_default

router = APIRouter(prefix="/v1/ev", tags=["ev"], dependencies=[Depends(require_token)])

SITE_COLS = "s.id, s.operator, s.name, s.network, s.address, s.city, s.postcode, s.lat, s.lon, s.facilities, s.open_24h, s.opening, s.seen"


def _iso(x: Any) -> str | None:
    return x.isoformat() if x else None


def _connector(text_: str | None) -> str | None:
    if not text_:
        return None
    want = N.asked_connector(text_)
    if want is None:
        raise HTTPException(422, f"no connector {text_!r}; try ccs, chademo or type2")
    return want


async def car_connector(engine: Any, vehicle: str | None = None) -> tuple[str | None, str | None]:
    """(connector, vehicle name): the named vehicle's, else the one your electric vehicles share. (None, None) when
    none is set, or your EVs take different ones."""
    async with engine.connect() as conn:
        if vehicle:
            row = (await conn.execute(text("SELECT name, connector FROM vehicle WHERE id = :v"), {"v": vehicle})).first()
            if row is None:
                raise HTTPException(404, f"no vehicle {vehicle!r}")
            return row.connector, row.name
        rows = (await conn.execute(text(
            "SELECT name, connector FROM vehicle WHERE kind = 'ev' AND connector IS NOT NULL ORDER BY name"))).all()  # fmt: skip
    kinds = {r.connector for r in rows}
    return (rows[0].connector, rows[0].name if len(rows) == 1 else "your electric vehicles") if len(kinds) == 1 else (None, None)


async def _want(request: Request, connector: str | None, vehicle: str | None) -> str | None:
    """The connector asked for; with none asked and a vehicle named, that vehicle's. "any" means all."""
    if connector and connector.strip().lower() in ("any", "all"):
        return None
    if connector:
        return _connector(connector)
    if vehicle:
        return (await car_connector(request.app.state.engine, vehicle))[0]
    return None


async def reliability(conn: Any, site_ids: list[str], days: int = 30) -> dict[str, dict[str, Any]]:
    """For each site, from the readings kept: how often at least one charger was out of order, how many on average
    and at worst, and the share of chargers working on average."""
    if not site_ids:
        return {}
    rows = await conn.execute(text(
        "SELECT site_id, count(*) AS n, avg((out_of_order > 0)::int) AS broken, avg(out_of_order) AS avg_out, "
        "max(out_of_order) AS worst, avg(working::float / nullif(total, 0)) AS working_share, min(at) AS since "
        "FROM ev_site_log WHERE site_id = ANY(:ids) AND at >= now() - make_interval(days => :d) GROUP BY site_id"),
        {"ids": site_ids, "d": days})  # fmt: skip
    return {r.site_id: {"days": days, "readings": r.n, "since": _iso(r.since),
                        "share_with_broken": round(float(r.broken or 0), 3),
                        "average_out_of_order": round(float(r.avg_out or 0), 1), "worst_out_of_order": r.worst,
                        "average_working_share": round(float(r.working_share), 3) if r.working_share is not None else None}
            for r in rows}  # fmt: skip


async def _details(conn: Any, site_ids: list[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, float]]:
    """Each site's chargers, and the prices by tariff id (as the connectors name them)."""
    by: dict[str, list[dict[str, Any]]] = {i: [] for i in site_ids}
    if site_ids:
        rows = await conn.execute(text(
            "SELECT id, site_id, status, connectors, max_kw, updated_at FROM ev_evse WHERE site_id = ANY(:ids) ORDER BY id"),
            {"ids": site_ids})  # fmt: skip
        for r in rows:
            by[r.site_id].append({"id": r.id, "status": r.status, "connectors": r.connectors or [], "max_kw": r.max_kw,
                                  "updated": _iso(r.updated_at)})  # fmt: skip
    prices = {}
    for r in await conn.execute(text("SELECT id, pence_per_kwh FROM ev_tariff WHERE pence_per_kwh IS NOT NULL")):
        prices[r.id.split(":", 1)[-1]] = r.pence_per_kwh
    return by, prices


def site_out(r: Any, chargers: list[dict[str, Any]], prices: dict[str, float], want: str | None,
             km: float | None = None, with_chargers: bool = False) -> dict[str, Any]:  # fmt: skip
    s = N.summary(chargers, prices, want)
    hours = N.hours_now(r.opening, datetime.now(UTC), ZoneInfo(get_settings().local_tz))
    out = {"id": r.id, "operator": r.operator, "name": r.name, "network": r.network, "address": r.address,
           "city": r.city, "postcode": r.postcode, "lat": r.lat, "lon": r.lon, "km": round(km, 1) if km is not None else None,
           "open_24h": r.open_24h, "facilities": list(r.facilities or []), "chargers": s,
           "open_now": hours["open"], "closes": hours["closes"], "opens": hours["opens"],
           "summary": N.sentence(r.name, s, km, want, hours), "updated": _iso(r.seen)}  # fmt: skip
    if with_chargers:
        out["charger_list"] = chargers
    return out


def _keep(site: dict[str, Any], want: str | None, free_only: bool) -> bool:
    c = site["chargers"]
    if want and not c["total"]:
        return False
    return not free_only or (c["free"] > 0 and site["open_now"] is not False)


@router.get("/sites")
async def sites(
    request: Request,
    place: str | None = None,
    km: float = Query(25.0, gt=0, le=500),
    connector: str | None = Query(None, description="ccs, chademo or type2: only chargers with that connector"),
    free: bool = Query(False, description="only sites with a free charger now"),
    vehicle: str | None = Query(None, description="a saved vehicle: its connector, when none is given"),
    q: str | None = Query(None, max_length=80, description="a name to look for (anywhere, not only near the place)"),
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    """Charging sites nearest a saved place (home by default), or whose name matches `q`."""
    want = await _want(request, connector, vehicle)
    pid = await place_or_default(request, place)
    async with request.app.state.engine.connect() as conn:
        p = (await conn.execute(text("SELECT id, name, geog FROM place WHERE id = :p"), {"p": pid})).first()
        if p is None:
            raise HTTPException(404, f"no place {pid!r}")
        where = "ST_DWithin(s.geog, pl.geog, :m)" if not q else "(s.name ILIKE :q OR s.city ILIKE :q OR s.postcode ILIKE :q)"
        rows = (await conn.execute(text(
            f"SELECT {SITE_COLS}, ST_Distance(s.geog, pl.geog) / 1000.0 AS km FROM ev_site s JOIN place pl ON pl.id = :p "
            f"WHERE {where} ORDER BY km LIMIT :n"), {"p": pid, "m": km * 1000, "q": f"%{q}%", "n": limit * 3})).all()  # fmt: skip
        by, prices = await _details(conn, [r.id for r in rows])
        rel = await reliability(conn, [r.id for r in rows])
        total = (await conn.execute(text("SELECT count(*), max(seen) FROM ev_site"))).first()
    items = [x for x in (site_out(r, by[r.id], prices, want, r.km) for r in rows) if _keep(x, want, free)][:limit]
    for x in items:
        x["reliability"] = rel.get(x["id"])
        x["reliability_summary"] = N.reliability_sentence(x["reliability"])
    return {"place": pid, "km": None if q else km, "q": q, "connector": want, "count": len(items),
            "stored": total[0], "updated": _iso(total[1]), "sites": items}  # fmt: skip


@router.get("/sites/{site_id}")
async def one_site(request: Request, site_id: str, connector: str | None = None,
                   days: int = Query(30, ge=1, le=400, description="how far back to judge reliability")) -> dict[str, Any]:  # fmt: skip
    """One site, with each charger and its status, and how reliable it has been."""
    want = _connector(connector)
    async with request.app.state.engine.connect() as conn:
        r = (await conn.execute(text(f"SELECT {SITE_COLS} FROM ev_site s WHERE s.id = :i"), {"i": site_id})).first()
        if r is None:
            raise HTTPException(404, f"no charging site {site_id!r}")
        by, prices = await _details(conn, [r.id])
        rel = (await reliability(conn, [r.id], days)).get(r.id)
    out = site_out(r, by[r.id], prices, want, with_chargers=True)
    out["reliability"] = rel
    out["reliability_summary"] = N.reliability_sentence(rel)
    return out


@router.get("/trips/{trip_id}")
async def along_trip(
    request: Request,
    trip_id: str,
    within_km: float = Query(2.0, gt=0, le=20, description="how far off the route a site may be"),
    connector: str | None = Query(None, description="ccs, chademo or type2"),
    free: bool = Query(False, description="only sites with a free charger now"),
    vehicle: str | None = Query(None, description="a saved vehicle: its connector, when none is given"),
) -> dict[str, Any]:
    """Charging sites along a trip's route, in the order you pass them, with how far along each is."""
    want = await _want(request, connector, vehicle)
    async with request.app.state.engine.connect() as conn:
        trip = (await conn.execute(text(TRIP_SQL), {"id": trip_id})).first()
        if trip is None:
            raise HTTPException(404, f"no trip {trip_id!r}")
        if not trip.corridor_id:
            raise HTTPException(409, f"{trip.name} has no route yet: build it first (POST /v1/trips/{trip_id}/route)")
        rows = (await conn.execute(text(
            f"WITH {ROUTE} SELECT {SITE_COLS}, ST_Distance(s.geog, r.line) / 1000.0 AS off_km, "
            "ST_LineLocatePoint(r.line::geometry, s.geog::geometry) AS along "
            "FROM ev_site s, route r WHERE ST_DWithin(s.geog, r.line, :m) ORDER BY along"),
            {"corridor": trip.corridor_id, "m": within_km * 1000})).all()  # fmt: skip
        by, prices = await _details(conn, [r.id for r in rows])
    items = []
    for r in rows:
        x = site_out(r, by[r.id], prices, want, r.off_km)
        if not _keep(x, want, free):
            continue
        x["km_along"] = round(r.along * trip.distance_km, 1) if trip.distance_km else None
        x["off_route_km"] = x.pop("km")
        hours = {"open": x["open_now"], "opens": x["opens"]}
        x["summary"] = N.sentence(r.name, x["chargers"], None, want, hours) + (
            f" {x['km_along']:.0f} km along." if x["km_along"] is not None else "")
        items.append(x)
    return {"trip": {"id": trip.id, "name": trip.name, "distance_km": trip.distance_km}, "within_km": within_km,
            "connector": want, "count": len(items), "sites": items}  # fmt: skip


@router.get("/all")
async def everywhere(request: Request) -> dict[str, Any]:
    """Every site, briefly: position, chargers working and free (in all and by connector), open now, the fastest,
    the price and how often one has been out of order lately. For Home Assistant, which picks what is near whoever
    is looking; and the connector your electric vehicles take, if they agree."""
    now = datetime.now(UTC)
    tz = ZoneInfo(get_settings().local_tz)
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(f"SELECT {SITE_COLS} FROM ev_site s ORDER BY s.name"))).all()
        by, prices = await _details(conn, [r.id for r in rows])
        rel = await reliability(conn, [r.id for r in rows])
        updated = (await conn.execute(text("SELECT max(seen) FROM ev_site"))).scalar()
    connector, whose = await car_connector(request.app.state.engine)
    out = []
    for r in rows:
        s = N.summary(by[r.id], prices)
        h = N.hours_now(r.opening, now, tz)
        p = s["pence_per_kwh"] or {}
        out.append({"id": r.id, "name": r.name, "postcode": r.postcode, "lat": round(r.lat, 5), "lon": round(r.lon, 5),
                    "total": s["total"], "working": s["working"], "free": s["free"], "open": h["open"],
                    "opens": h["opens"], "kw": s["max_kw"], "p": p.get("min"), "p_max": p.get("max"),
                    "by": {k: [v["total"], v["working"], v["free"], v["max_kw"]] for k, v in s["connectors"].items()},
                    "broken": (rel.get(r.id) or {}).get("share_with_broken")})  # fmt: skip
    return {"updated": _iso(updated), "my_connector": connector, "my_vehicle": whose, "count": len(out), "sites": out}

"""EV chargers: sites near a place or along a trip, how many chargers work and are free, by connector, and prices.

GRIDSERVE's network for now (app.integrations.gridserve); the tables keep an `operator` so others can join.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.conditions import ROUTE, TRIP_SQL
from app.integrations.gridserve import normalise as N
from app.places import place_or_default

router = APIRouter(prefix="/v1/ev", tags=["ev"], dependencies=[Depends(require_token)])

SITE_COLS = "s.id, s.operator, s.name, s.network, s.address, s.city, s.postcode, s.lat, s.lon, s.facilities, s.open_24h, s.seen"


def _iso(x: Any) -> str | None:
    return x.isoformat() if x else None


def _connector(text_: str | None) -> str | None:
    if not text_:
        return None
    want = N.asked_connector(text_)
    if want is None:
        raise HTTPException(422, f"no connector {text_!r}; try ccs, chademo or type2")
    return want


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
    out = {"id": r.id, "operator": r.operator, "name": r.name, "network": r.network, "address": r.address,
           "city": r.city, "postcode": r.postcode, "lat": r.lat, "lon": r.lon, "km": round(km, 1) if km is not None else None,
           "open_24h": r.open_24h, "facilities": list(r.facilities or []), "chargers": s,
           "summary": N.sentence(r.name, s, km, want), "updated": _iso(r.seen)}  # fmt: skip
    if with_chargers:
        out["charger_list"] = chargers
    return out


def _keep(site: dict[str, Any], want: str | None, free_only: bool) -> bool:
    c = site["chargers"]
    if want and not c["total"]:
        return False
    return not free_only or c["free"] > 0


@router.get("/sites")
async def sites(
    request: Request,
    place: str | None = None,
    km: float = Query(25.0, gt=0, le=500),
    connector: str | None = Query(None, description="ccs, chademo or type2: only chargers with that connector"),
    free: bool = Query(False, description="only sites with a free charger now"),
    q: str | None = Query(None, max_length=80, description="a name to look for (anywhere, not only near the place)"),
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    """Charging sites nearest a saved place (home by default), or whose name matches `q`."""
    want = _connector(connector)
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
        total = (await conn.execute(text("SELECT count(*), max(seen) FROM ev_site"))).first()
    items = [x for x in (site_out(r, by[r.id], prices, want, r.km) for r in rows) if _keep(x, want, free)][:limit]
    return {"place": pid, "km": None if q else km, "q": q, "connector": want, "count": len(items),
            "stored": total[0], "updated": _iso(total[1]), "sites": items}  # fmt: skip


@router.get("/sites/{site_id}")
async def one_site(request: Request, site_id: str, connector: str | None = None) -> dict[str, Any]:
    """One site, with each charger and its status."""
    want = _connector(connector)
    async with request.app.state.engine.connect() as conn:
        r = (await conn.execute(text(f"SELECT {SITE_COLS} FROM ev_site s WHERE s.id = :i"), {"i": site_id})).first()
        if r is None:
            raise HTTPException(404, f"no charging site {site_id!r}")
        by, prices = await _details(conn, [r.id])
    return site_out(r, by[r.id], prices, want, with_chargers=True)


@router.get("/trips/{trip_id}")
async def along_trip(
    request: Request,
    trip_id: str,
    within_km: float = Query(2.0, gt=0, le=20, description="how far off the route a site may be"),
    connector: str | None = Query(None, description="ccs, chademo or type2"),
    free: bool = Query(False, description="only sites with a free charger now"),
) -> dict[str, Any]:
    """Charging sites along a trip's route, in the order you pass them, with how far along each is."""
    want = _connector(connector)
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
        x["summary"] = N.sentence(r.name, x["chargers"], None, want) + (
            f" {x['km_along']:.0f} km along." if x["km_along"] is not None else "")
        items.append(x)
    return {"trip": {"id": trip.id, "name": trip.name, "distance_km": trip.distance_km}, "within_km": within_km,
            "connector": want, "count": len(items), "sites": items}  # fmt: skip

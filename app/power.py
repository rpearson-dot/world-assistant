"""Power: cuts and works near your places, what supplies them, and how clean the electricity is (UK Power Networks)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.integrations.ukpn.normalise import cleanest
from app.places import place_or_default

router = APIRouter(prefix="/v1/power", tags=["power"], dependencies=[Depends(require_token)])
NEAR_KM = 1.0  # a power cut this close to a place, or naming its postcode, counts as at the place


def _iso(x: Any) -> str | None:
    return x.isoformat() if x else None


def cut_row(r: Any) -> dict[str, Any]:
    return {"id": r.id, "planned": r.planned, "status": r.status, "description": r.description,
            "postcodes": list(r.postcodes or [])[:30], "customers": r.customers, "started": _iso(r.started_at),
            "restore_by": _iso(r.restore_at), "km": round(r.km, 2) if r.km is not None else None,
            "at_place": bool(r.here), "lat": r.lat, "lon": r.lon}  # fmt: skip


def works_row(r: Any) -> dict[str, Any]:
    return {"id": r.id, "stage": r.stage, "street": r.street, "town": r.town, "usrn": r.usrn, "category": r.category,
            "traffic": r.traffic, "description": r.description, "starts": _iso(r.starts_at), "ends": _iso(r.ends_at),
            "km": round(r.km, 2), "lat": r.lat, "lon": r.lon}  # fmt: skip


async def _place(request: Request, place: str | None) -> Any:
    pid = await place_or_default(request, place)
    async with request.app.state.engine.connect() as conn:
        return (await conn.execute(text("SELECT id, name, postcode, lat, lon FROM place WHERE id = :p"), {"p": pid})).first()


async def cuts_near(conn: Any, p: Any, km: float) -> list[dict[str, Any]]:
    """Power cuts within km of a place, or naming its postcode (a cut with no position is matched by postcode)."""
    from app.integrations.ukpn.sync import AT

    here = AT.replace(":m", ":here_m")
    rows = await conn.execute(text(
        f"SELECT f.*, ST_Distance(f.geog, p.geog) / 1000.0 AS km, {here} AS here "
        f"FROM ukpn_fault f JOIN place p ON p.id = :p WHERE {here} OR ST_DWithin(f.geog, p.geog, :m) "
        "ORDER BY here DESC, km NULLS FIRST"), {"p": p.id, "m": km * 1000, "here_m": NEAR_KM * 1000})  # fmt: skip
    return [cut_row(r) for r in rows]


@router.get("/cuts")
async def cuts(request: Request, place: str | None = None, km: float = Query(5.0, gt=0, le=50)) -> dict[str, Any]:
    """Power cuts now near a saved place (home by default), the ones at the place first."""
    p = await _place(request, place)
    async with request.app.state.engine.connect() as conn:
        items = await cuts_near(conn, p, km)
        total = (await conn.execute(text("SELECT count(*), max(seen) FROM ukpn_fault"))).first()
    return {"place": p.id, "count": len(items), "at_place": sum(i["at_place"] for i in items),
            "across_network": total[0], "updated": _iso(total[1]), "cuts": items}  # fmt: skip


@router.get("/works")
async def works(request: Request, place: str | None = None, km: float = Query(1.0, gt=0, le=20),
                stage: str | None = Query(None, pattern="^(open|proposed)$")) -> dict[str, Any]:  # fmt: skip
    """UK Power Networks' street works near a place: under way (open) and planned (proposed)."""
    p = await _place(request, place)
    where = "" if not stage else "AND w.stage = :stage "
    async with request.app.state.engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT w.*, ST_Distance(w.geog, pl.geog) / 1000.0 AS km FROM ukpn_works w JOIN place pl ON pl.id = :p "
            f"WHERE ST_DWithin(w.geog, pl.geog, :m) AND (w.stage = 'open' OR w.ends_at IS NULL OR w.ends_at >= now()) {where}"
            "ORDER BY w.stage, km LIMIT 300"), {"p": p.id, "m": km * 1000, "stage": stage})  # fmt: skip
        items = [works_row(r) for r in rows]
    return {"place": p.id, "count": len(items), "open": sum(i["stage"] == "open" for i in items),
            "proposed": sum(i["stage"] == "proposed" for i in items), "works": items}  # fmt: skip


async def carbon_for(conn: Any, place_id: str, hours: float = 3.0) -> dict[str, Any] | None:
    gsp = (await conn.execute(text("SELECT name FROM ukpn_place_area WHERE place_id = :p AND kind = 'gsp' LIMIT 1"),
                              {"p": place_id})).scalar()  # fmt: skip
    if not gsp:
        return None
    rows = [(r[0], r[1]) for r in await conn.execute(
        text("SELECT at, intensity FROM ukpn_carbon WHERE gsp = :g AND at >= now() - interval '1 hour' ORDER BY at"),
        {"g": gsp})]  # fmt: skip
    best = cleanest(rows, datetime.now(UTC), hours)
    return {"grid_supply_point": gsp, "unit": "gCO2/kWh", **(best or {"now": None})}


@router.get("/carbon")
async def carbon(request: Request, place: str | None = None, hours: float = Query(3.0, ge=0.5, le=12)) -> dict[str, Any]:
    """How clean the electricity is at the grid supply point feeding a place, now, and its cleanest block ahead."""
    p = await _place(request, place)
    async with request.app.state.engine.connect() as conn:
        out = await carbon_for(conn, p.id, hours)
    return {"place": p.id, "available": bool(out and out.get("now") is not None), **(out or {})}


@router.get("/supply")
async def supply(request: Request, place: str | None = None) -> dict[str, Any]:
    """What supplies a place: whether it is on UK Power Networks' network, the grid supply point, primary and
    secondary substation feeding it, its rota load disconnection block, power cuts there now, and carbon intensity."""
    p = await _place(request, place)
    async with request.app.state.engine.connect() as conn:
        areas = (await conn.execute(text("SELECT kind, code, name FROM ukpn_place_area WHERE place_id = :p ORDER BY kind"),
                                    {"p": p.id})).all()  # fmt: skip
        by: dict[str, list[dict[str, str]]] = {}
        for a in areas:
            by.setdefault(a.kind, []).append({"code": a.code, "name": a.name})
        here = [c for c in await cuts_near(conn, p, NEAR_KM) if c["at_place"]]
        carbon_now = await carbon_for(conn, p.id)
    checked = bool(areas)
    on_network = bool(by.get("boundary")) and not by.get("idno")
    return {
        "place": p.id, "checked": checked,
        "on_network": on_network if checked else None,
        "independent_operator": (by.get("idno") or [{}])[0].get("name") if by.get("idno") else None,
        "grid_supply_point": (by.get("gsp") or [None])[0], "primary": (by.get("primary") or [None])[0],
        "secondary": (by.get("secondary") or [None])[0], "rota_block": (by.get("rota") or [None])[0],
        "power_cut_now": bool(here), "cuts": here, "carbon": carbon_now,
    }  # fmt: skip


def history_row(r: Any) -> dict[str, Any]:
    end = r.ended_at or r.last_seen
    start = r.started_at or r.first_seen
    return {"id": r.id, "planned": r.planned, "description": r.description, "postcodes": list(r.postcodes or [])[:30],
            "customers": r.customers_max, "started": _iso(start), "ended": _iso(r.ended_at), "ongoing": r.ended_at is None,
            "hours": round((end - start).total_seconds() / 3600, 1) if end and start else None,
            "first_promised": _iso(r.first_restore_at), "last_promised": _iso(r.last_restore_at),
            "late": bool(r.ended_at and r.first_restore_at and r.ended_at > r.first_restore_at),
            "places": list(r.places or []), "home": r.home}  # fmt: skip


@router.get("/history")
async def history(request: Request, place: str | None = None, days: int = Query(365, ge=1, le=3650),
                  limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:  # fmt: skip
    """Every power cut recorded at a saved place (home by default) in the last `days`: how long it lasted, how many
    homes, and whether power came back later than first promised. Kept for good (ukpn_fault_history)."""
    pid = await place_or_default(request, place)
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT * FROM ukpn_fault_history WHERE :p = ANY(places) AND first_seen >= now() - make_interval(days => :d) "
            "ORDER BY first_seen DESC LIMIT :limit"), {"p": pid, "d": days, "limit": limit})).all()  # fmt: skip
        recorded = (await conn.execute(text("SELECT min(first_seen), count(*) FROM ukpn_fault_history"))).first()
    items = [history_row(r) for r in rows]
    unplanned = [i for i in items if i["planned"] is not True]
    hours = [i["hours"] for i in unplanned if i["hours"] is not None and not i["ongoing"]]
    return {"place": pid, "days": days, "count": len(items), "unplanned": len(unplanned),
            "planned": len(items) - len(unplanned), "total_hours_unplanned": round(sum(hours), 1),
            "longest_hours": max(hours, default=None), "late": sum(i["late"] for i in items),
            "recording_since": _iso(recorded[0]), "recorded_network_wide": recorded[1], "cuts": items}  # fmt: skip

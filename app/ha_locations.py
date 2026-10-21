"""Extra places Home Assistant can ask World Assistant to watch for events.

Home Assistant's home zone is handled by Home Assistant itself (it fetches events for zone.home). This
is for the others: it adds a place, the worker publishes a sensor of nearby events for it over MQTT,
and the events dashboard lists every such sensor it finds.

Each place costs a search on every events source every `ha_location_refresh_h` hours, so there is a cap,
and places expire unless Home Assistant renews them (adding the same name again). When the cap is 75%
used the time-out shortens so that forgotten places clear sooner.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.events import search
from app.ha import slug

router = APIRouter(prefix="/v1/ha/locations", tags=["home-assistant"], dependencies=[Depends(require_token)])


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    lat: float = Field(ge=49.5, le=61.0)
    lon: float = Field(ge=-8.7, le=2.0)
    radius_km: float | None = Field(default=None, gt=0, le=50)


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def limits(count: int) -> dict[str, Any]:
    """The cap, the time-out in force, and the provider calls the current places cost per day."""
    s = get_settings()
    soft = math.ceil(s.ha_locations_max * 0.75)
    ttl = min(s.ha_location_ttl_days, s.ha_location_pressure_ttl_days) if count >= soft else s.ha_location_ttl_days
    sources = sum(bool(k) for k in (s.skiddle_api_key, s.ticketmaster_api_key))
    per_day = count * math.ceil(24 / max(1, s.ha_location_refresh_h)) * max(1, sources)
    return {"count": count, "max": s.ha_locations_max, "pressure_at": soft, "ttl_days": ttl,
            "refresh_hours": s.ha_location_refresh_h, "provider_calls_per_day": per_day}  # fmt: skip


async def sweep(request: Request) -> int:
    """Drop places whose time-out has passed. Returns how many went."""
    async with request.app.state.engine.begin() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM ha_location"))).scalar_one()
        ttl = limits(int(count))["ttl_days"]
        cutoff = datetime.now(UTC) - timedelta(days=ttl)
        res = await conn.execute(text("DELETE FROM ha_location WHERE last_seen < :c"), {"c": cutoff})
    return int(res.rowcount or 0)


async def _load(request: Request) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    await sweep(request)
    async with request.app.state.engine.connect() as conn:
        rows = [dict(r._mapping) for r in await conn.execute(text(
            "SELECT id, name, lat, lon, radius_km, created_at, last_seen FROM ha_location ORDER BY name"))]  # fmt: skip
    lim = limits(len(rows))
    for r in rows:
        r["expires_at"] = r["last_seen"] + timedelta(days=lim["ttl_days"])
    return rows, lim


@router.get("")
async def list_locations(request: Request) -> dict[str, Any]:
    rows, lim = await _load(request)
    return {"locations": rows, "limits": lim}


@router.post("")
async def add_location(body: LocationIn, request: Request) -> dict[str, Any]:
    """Add a place, or renew one with the same name (which also updates its position)."""
    loc_id = slug(body.name)
    if not loc_id:
        raise HTTPException(422, "the name needs letters or digits")
    rows, lim = await _load(request)
    if loc_id not in {r["id"] for r in rows} and len(rows) >= lim["max"]:
        raise HTTPException(
            409, f"already watching the maximum of {lim['max']} places ({', '.join(r['name'] for r in rows)}): "
                 "remove one first, or wait for one to time out")  # fmt: skip
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text(
            """INSERT INTO ha_location (id, name, lat, lon, radius_km) VALUES (:id, :name, :lat, :lon, :r)
               ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
                 radius_km = EXCLUDED.radius_km, last_seen = now()"""),
            {"id": loc_id, "name": body.name, "lat": body.lat, "lon": body.lon, "r": body.radius_km})  # fmt: skip
    return {"id": loc_id, "name": body.name, "entity": f"sensor.wa_events_place_{loc_id}", "limits": limits(len(rows) + (loc_id not in {r["id"] for r in rows}))}  # fmt: skip


@router.post("/delete")
async def delete_by_name(body: NameIn, request: Request) -> dict[str, Any]:
    return await _delete(request, slug(body.name))


@router.delete("/{loc_id}")
async def delete_location(loc_id: str, request: Request) -> dict[str, Any]:
    return await _delete(request, loc_id)


async def _delete(request: Request, loc_id: str) -> dict[str, Any]:
    async with request.app.state.engine.begin() as conn:
        res = await conn.execute(text("DELETE FROM ha_location WHERE id = :id"), {"id": loc_id})
    if not res.rowcount:
        raise HTTPException(404, "no such location")
    return {"deleted": loc_id}


@router.get("/events")
async def location_events(request: Request) -> dict[str, Any]:
    """Events near each place. Each search is cached for the refresh interval, so calling this often
    does not cost provider requests."""
    s = get_settings()
    rows, lim = await _load(request)
    out = []
    for r in rows:
        try:
            res = await search(
                request, r["name"], r["lat"], r["lon"], radius_km=r["radius_km"] or s.ha_location_radius_km,
                days=s.ha_location_days, type=None, keyword=None, limit=20,
                cache_seconds=max(1, s.ha_location_refresh_h) * 3600)  # fmt: skip
            events, errors = res["events"], res["errors"]
        except HTTPException as exc:  # no source set up, or every source failed: keep the place, no events
            events, errors = [], [str(exc.detail)]
        out.append({"id": r["id"], "name": r["name"], "lat": r["lat"], "lon": r["lon"],
                    "radius_km": r["radius_km"] or s.ha_location_radius_km, "days": s.ha_location_days,
                    "expires_at": r["expires_at"], "count": len(events), "events": events, "errors": errors})  # fmt: skip
    return {"locations": out, "limits": lim}

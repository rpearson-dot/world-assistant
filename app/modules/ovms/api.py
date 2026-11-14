"""The car's OVMS data (/v1/ovms): each car's state and warnings, its notifications and a metric's history, and the
settings. Reading only: commands (lock, climate) wait for the MQTT broker to have TLS."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.jobs import tracked
from app.modules.ovms import listener, parse, store

router = APIRouter(prefix="/v1/ovms", tags=["ovms"], dependencies=[Depends(require_token)])


@router.get("")
async def overview(request: Request) -> dict[str, Any]:
    """Every car heard from: battery, range, 12 V, charging, locked, where, how recently it reported, its warnings
    now and its last notifications. `vehicle` links an OVMS vehicle id to a vehicle on the Vehicles page."""
    async with request.app.state.engine.connect() as conn:
        st = await store.settings(conn)
        out = []
        for v in await store.vehicles(conn):
            m = await store.metrics(conn, v)
            out.append({"id": v, "vehicle": (st.get("vehicles") or {}).get(v), **parse.summary(m),
                        "warnings": parse.warnings(m, datetime.now(UTC)), "notices": await store.notices(conn, v, 14, 10)})  # fmt: skip
    return {"settings": st, "listening": listener.LISTENER.listening, "cars": out}


@router.get("/{vehicle}/metrics")
async def all_metrics(request: Request, vehicle: str) -> dict[str, Any]:
    """Every metric the car has sent, as it stands, with when."""
    async with request.app.state.engine.connect() as conn:
        m = await store.metrics(conn, vehicle)
    return {"vehicle": vehicle, "metrics": {k: {"value": v, "at": at} for k, (v, at) in sorted(m.items())}}


@router.get("/{vehicle}/history")
async def metric_history(request: Request, vehicle: str, metric: str = Query(..., max_length=80),
                         days: int = Query(7, ge=1, le=730)) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT at, value FROM ovms_metric_log WHERE vehicle = :v AND metric = :m AND at > now() - make_interval(days => :d) "
            "ORDER BY at"), {"v": vehicle, "m": metric, "d": days})).all()  # fmt: skip
    return {"vehicle": vehicle, "metric": metric, "points": [{"at": r[0], "value": r[1]} for r in rows]}


@router.get("/{vehicle}/notices")
async def notices(request: Request, vehicle: str, days: int = Query(30, ge=1, le=730)) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        return {"vehicle": vehicle, "notices": await store.notices(conn, vehicle, days, 1000)}


class SettingsIn(BaseModel):
    enabled: bool = True
    prefix: str = Field("ovms/+/+/", max_length=120)
    vehicles: dict[str, str] = Field(default_factory=dict)


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """Whether to listen, the topic prefix the car publishes under (OVMS: Config → Server V3 → topic prefix; "+" for
    any part), and which Vehicles-page vehicle each OVMS vehicle id is."""
    p = body.prefix.strip()
    if not p or "#" in p or any(c.isspace() for c in p) or any(part not in ("+",) and "+" in part for part in p.split("/")):
        raise HTTPException(422, "a topic prefix such as ovms/+/+/ or ovms/rob/HV66BZD/")
    st = {"enabled": body.enabled, "prefix": p if p.endswith("/") else p + "/", "vehicles": dict(list(body.vehicles.items())[:20])}
    async with request.app.state.engine.begin() as conn:
        await store.save_settings(conn, st)
    if listener.LISTENER.task is not None:  # listen again with the new prefix
        await listener.LISTENER.stop()
        await listener.LISTENER.start(request.app)
    return st


async def ovms_housekeep(ctx: dict[str, Any]) -> Any:
    async def work() -> dict[str, Any]:
        async with ctx["engine"].begin() as conn:
            return await store.housekeep(conn)

    return await tracked(ctx["engine"], "ovms_housekeep", work)


async def ovms_watch(ctx: dict[str, Any]) -> Any:
    """Every 15 minutes: warnings that come from time passing (the car gone quiet), which no message would raise."""
    engine = ctx["engine"]
    async with engine.connect() as conn:
        cars = await store.vehicles(conn)
    for v in cars:
        await listener.check(engine, v)
    return {"cars": len(cars)} if cars else None

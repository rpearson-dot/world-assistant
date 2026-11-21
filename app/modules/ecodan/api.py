"""The Ecodan heat pump (/v1/ecodan, the Energy → Heat pump tab): tank temperature and target, the hot-water boost
state, flow/return and outdoor temperatures, the heating zone, power and daily energy, read from MELCloud every ten
minutes and kept. Tracking only — no control (that comes in a later phase, shadow-first). See claude/ecodan-design.md.

MELCloud throttles hard, so the poll is every 10 minutes; the email and password come from the Keys page
(melcloud_email, melcloud_password).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings, read_secret
from app.jobs import tracked
from app.modules.ecodan import melcloud

log = logging.getLogger("wa.ecodan")
router = APIRouter(prefix="/v1/ecodan", tags=["ecodan"], dependencies=[Depends(require_token)])
KEYS = ("melcloud_email", "melcloud_password")
COLS = ("name", "tank_c", "tank_target_c", "forced_hot_water", "flow_c", "return_c", "outdoor_c", "zone1_room_c",
        "zone1_set_c", "power", "operation_mode", "heating_in_kwh", "water_in_kwh", "heating_out_kwh", "water_out_kwh",
        "offline")  # fmt: skip


def _creds() -> dict[str, str]:
    return {k: read_secret(k) for k in KEYS}


async def latest(conn: Any) -> list[dict[str, Any]]:
    cols = ", ".join(COLS)
    rows = (await conn.execute(text(
        f"SELECT DISTINCT ON (device) device, at, {cols} FROM ecodan_reading ORDER BY device, at DESC"))).mappings().all()  # fmt: skip
    return [dict(r) for r in rows]


@router.get("")
async def state(request: Request) -> dict[str, Any]:
    """Each heat pump as it stands, and whether the MELCloud login is set."""
    configured = all(_creds().values())
    async with request.app.state.engine.connect() as conn:
        cur = await latest(conn)
    out: dict[str, Any] = {"units": cur, "configured": configured}
    if not configured:
        out["not_set_up"] = "Add the MELCloud login on the Keys page: melcloud_email and melcloud_password"
    return out


@router.get("/history")
async def history(request: Request, device: str, days: int = 7) -> dict[str, Any]:
    """A heat pump's readings over a day/week/month, for charting (tank, flow/return, outdoor, hot-water boost)."""
    days = max(1, min(days, 90))
    since = datetime.now(UTC) - timedelta(days=days)
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT at, tank_c, tank_target_c, flow_c, return_c, outdoor_c, forced_hot_water "
            "FROM ecodan_reading WHERE device = :d AND at >= :s ORDER BY at"), {"d": device, "s": since})).mappings().all()  # fmt: skip
    return {"points": [dict(r) for r in rows]}


@router.post("/refresh")
async def refresh(request: Request) -> dict[str, Any]:
    """Read the heat pump now (uses one of MELCloud's sparse allowed calls)."""
    return await tracked(request.app.state.engine, "ecodan_sync", lambda: sync(request.app.state.engine))


async def sync(engine: Any, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    creds = _creds()
    if not all(creds.values()):
        return {"not_set_up": "MELCloud login not set (Keys page: melcloud_email, melcloud_password)"}
    own = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": "WorldAssistant"})
    try:
        units = await melcloud.read(client, creds["melcloud_email"], creds["melcloud_password"])
    finally:
        if own:
            await client.aclose()
    now = datetime.now(UTC)
    if units:
        placeholders = ", ".join(f":{c}" for c in COLS)
        async with engine.begin() as conn:
            await conn.execute(text(
                f"INSERT INTO ecodan_reading (device, at, {', '.join(COLS)}) VALUES (:device, :at, {placeholders}) "
                "ON CONFLICT (device, at) DO NOTHING"), [{**u, "at": now} for u in units])  # fmt: skip
            await conn.execute(text("DELETE FROM ecodan_reading WHERE at < now() - interval '400 days'"))
    out: dict[str, Any] = {"units": len(units)}
    try:
        await ha_publish(engine, units)
    except Exception as exc:  # noqa: BLE001 - Home Assistant is optional
        out["ha"] = str(exc)[:120]
    return out


async def ha_publish(engine: Any, units: list[dict[str, Any]]) -> None:
    """Home Assistant sensors (read-only): tank temperature and target, flow/return and outdoor, and a hot-water-boost
    binary sensor."""
    from app.mqtt import publish
    from app.mqtt_config import load

    s = get_settings()
    conf, _ = await load(engine)
    if not conf.active or not units:
        return
    msgs: list[tuple[str, str, bool]] = []
    for u in units:
        dev_id = u["device"]
        topic = f"{s.mqtt_topic_prefix}/ecodan/{dev_id}/state"
        dev = {"identifiers": [f"world_assistant_ecodan_{dev_id}"], "name": u["name"], "manufacturer": "Mitsubishi Ecodan (via World Assistant)"}
        for key, name, field in [("tank", "Tank temperature", "tank_c"), ("tank_target", "Tank target", "tank_target_c"),
                                 ("flow", "Flow temperature", "flow_c"), ("return", "Return temperature", "return_c"),
                                 ("outdoor", "Outdoor temperature", "outdoor_c")]:
            uid = f"wa_ecodan_{dev_id}_{key}"
            cfg = {"name": name, "unique_id": uid, "default_entity_id": f"sensor.{uid}", "device": dev,
                   "state_topic": topic, "value_template": f"{{{{ value_json.{field} }}}}", "unit_of_measurement": "°C",
                   "device_class": "temperature", "state_class": "measurement"}  # fmt: skip
            msgs.append((f"{s.mqtt_discovery_prefix}/sensor/{uid}/config", json.dumps(cfg), True))
        uid = f"wa_ecodan_{dev_id}_hotwater"
        cfg = {"name": "Hot water boost", "unique_id": uid, "default_entity_id": f"binary_sensor.{uid}", "device": dev,
               "state_topic": topic, "value_template": "{{ 'ON' if value_json.forced_hot_water else 'OFF' }}",
               "device_class": "running", "payload_on": "ON", "payload_off": "OFF"}  # fmt: skip
        msgs.append((f"{s.mqtt_discovery_prefix}/binary_sensor/{uid}/config", json.dumps(cfg), True))
        msgs.append((topic, json.dumps(u, default=str), True))
    await publish(msgs, conf)


async def ecodan_sync(ctx: dict[str, Any]) -> Any:
    """Every ten minutes (MELCloud throttles): read the heat pump and keep it."""
    return await tracked(ctx["engine"], "ecodan_sync", lambda: sync(ctx["engine"]))

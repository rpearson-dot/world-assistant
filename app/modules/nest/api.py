"""The Nest thermostat (/v1/nest, the In the home → Heating tab): room temperature, humidity, the target, the mode and
whether it is calling for heat, read from Google's Smart Device Management API every few minutes and kept. Tracking only
— no control. The one-time Google Device Access setup (the $5 registration and OAuth client) mints the four keys this
reads from the Keys page: nest_project_id, nest_client_id, nest_client_secret, nest_refresh_token.
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
from app.modules.nest import sdm

log = logging.getLogger("wa.nest")
router = APIRouter(prefix="/v1/nest", tags=["nest"], dependencies=[Depends(require_token)])
KEYS = ("nest_project_id", "nest_client_id", "nest_client_secret", "nest_refresh_token")


def _creds() -> dict[str, str]:
    return {k: read_secret(k) for k in KEYS}


async def latest(conn: Any) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(
        "SELECT DISTINCT ON (device) device, name, at, ambient_c, humidity, setpoint_c, mode, hvac, eco, online "
        "FROM nest_reading ORDER BY device, at DESC"))).mappings().all()  # fmt: skip
    return [dict(r) for r in rows]


@router.get("")
async def state(request: Request) -> dict[str, Any]:
    """Each thermostat as it stands, and whether the Google keys are set."""
    configured = all(_creds().values())
    async with request.app.state.engine.connect() as conn:
        cur = await latest(conn)
    out: dict[str, Any] = {"thermostats": cur, "configured": configured}
    if not configured:
        out["not_set_up"] = ("Add the Google SDM keys on the Keys page: nest_project_id, nest_client_id, "
                             "nest_client_secret, nest_refresh_token")
    return out


@router.get("/history")
async def history(request: Request, device: str, days: int = 7) -> dict[str, Any]:
    """A thermostat's readings over a day/week/month, for charting (room temp, target, humidity, heating on/off)."""
    days = max(1, min(days, 90))
    since = datetime.now(UTC) - timedelta(days=days)
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT at, ambient_c, setpoint_c, humidity, hvac FROM nest_reading WHERE device = :d AND at >= :s "
            "ORDER BY at"), {"d": device, "s": since})).mappings().all()  # fmt: skip
    return {"points": [dict(r) for r in rows]}


@router.post("/refresh")
async def refresh(request: Request) -> dict[str, Any]:
    """Read the thermostat now."""
    return await tracked(request.app.state.engine, "nest_sync", lambda: sync(request.app.state.engine))


async def sync(engine: Any, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    creds = _creds()
    if not all(creds.values()):
        return {"not_set_up": "Nest keys not set (Keys page: nest_project_id, nest_client_id, nest_client_secret, nest_refresh_token)"}
    own = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": "WorldAssistant"})
    try:
        readings = await sdm.read(client, creds["nest_project_id"], creds["nest_client_id"],
                                  creds["nest_client_secret"], creds["nest_refresh_token"])
    finally:
        if own:
            await client.aclose()
    now = datetime.now(UTC)
    if readings:
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO nest_reading (device, at, name, ambient_c, humidity, setpoint_c, mode, hvac, eco, online) "
                "VALUES (:device, :at, :name, :ambient_c, :humidity, :setpoint_c, :mode, :hvac, :eco, :online) "
                "ON CONFLICT (device, at) DO NOTHING"), [{**r, "at": now} for r in readings])  # fmt: skip
            await conn.execute(text("DELETE FROM nest_reading WHERE at < now() - interval '400 days'"))
    out: dict[str, Any] = {"thermostats": len(readings)}
    try:
        await ha_publish(engine, readings)
    except Exception as exc:  # noqa: BLE001 - Home Assistant is optional
        out["ha"] = str(exc)[:120]
    return out


async def ha_publish(engine: Any, readings: list[dict[str, Any]]) -> None:
    """Home Assistant sensors (read-only): room temperature, humidity, the target, and a heating binary sensor."""
    from app.mqtt import publish
    from app.mqtt_config import load

    s = get_settings()
    conf, _ = await load(engine)
    if not conf.active or not readings:
        return
    msgs: list[tuple[str, str, bool]] = []
    for r in readings:
        dev_id = r["device"]
        topic = f"{s.mqtt_topic_prefix}/nest/{dev_id}/state"
        dev = {"identifiers": [f"world_assistant_nest_{dev_id}"], "name": r["name"], "manufacturer": "Nest (via World Assistant)"}
        for key, name, field, unit in [("temperature", "Temperature", "ambient_c", "°C"),
                                        ("humidity", "Humidity", "humidity", "%"),
                                        ("setpoint", "Target", "setpoint_c", "°C")]:
            uid = f"wa_nest_{dev_id}_{key}"
            cfg = {"name": name, "unique_id": uid, "default_entity_id": f"sensor.{uid}", "device": dev,
                   "state_topic": topic, "value_template": f"{{{{ value_json.{field} }}}}", "unit_of_measurement": unit,
                   "device_class": "humidity" if key == "humidity" else "temperature", "state_class": "measurement"}  # fmt: skip
            msgs.append((f"{s.mqtt_discovery_prefix}/sensor/{uid}/config", json.dumps(cfg), True))
        uid = f"wa_nest_{dev_id}_heating"
        cfg = {"name": "Heating", "unique_id": uid, "default_entity_id": f"binary_sensor.{uid}", "device": dev,
               "state_topic": topic, "value_template": "{{ 'ON' if value_json.hvac == 'HEATING' else 'OFF' }}",
               "device_class": "heat", "payload_on": "ON", "payload_off": "OFF"}  # fmt: skip
        msgs.append((f"{s.mqtt_discovery_prefix}/binary_sensor/{uid}/config", json.dumps(cfg), True))
        msgs.append((topic, json.dumps(r, default=str), True))
    await publish(msgs, conf)


async def nest_sync(ctx: dict[str, Any]) -> Any:
    """Every few minutes: read the thermostat and keep it."""
    return await tracked(ctx["engine"], "nest_sync", lambda: sync(ctx["engine"]))

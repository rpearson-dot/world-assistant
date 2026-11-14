"""The charger in Home Assistant, over MQTT: its status, power, energy and connection as sensors, and a "charging"
switch that starts and stops a session through World Assistant. The api process holding the charger's link
publishes (on every change) and listens for the switch.

Entity ids: sensor.wa_ocpp_status, sensor.wa_ocpp_power, sensor.wa_ocpp_energy, sensor.wa_ocpp_session_energy,
sensor.wa_ocpp_current, sensor.wa_ocpp_error, sensor.wa_ocpp_last_boot, binary_sensor.wa_ocpp_connected,
switch.wa_ocpp_charging. The charging plan (homeassistant/packages/wa_ev_plan.yaml) uses them when they are there.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.mqtt import publish
from app.mqtt_config import client, load

if TYPE_CHECKING:
    from app.modules.ocpp.server import Hub

log = logging.getLogger("wa.ocpp.ha")

SENSORS: list[tuple[str, str, dict[str, Any]]] = [
    ("status", "Charger status", {"value_template": "{{ value_json.status }}", "icon": "mdi:ev-station"}),
    ("power", "Charger power", {"value_template": "{{ value_json.power_kw }}", "unit_of_measurement": "kW",
                                "device_class": "power", "state_class": "measurement"}),
    ("energy", "Charger energy meter", {"value_template": "{{ value_json.energy_kwh }}", "unit_of_measurement": "kWh",
                                        "device_class": "energy", "state_class": "total_increasing"}),
    ("session_energy", "Charger session energy", {"value_template": "{{ value_json.session_kwh }}", "unit_of_measurement": "kWh",
                                                  "device_class": "energy", "state_class": "total"}),
    ("current", "Charger current", {"value_template": "{{ value_json.current_a }}", "unit_of_measurement": "A",
                                    "device_class": "current", "state_class": "measurement"}),
    ("error", "Charger fault", {"value_template": "{{ value_json.error }}", "icon": "mdi:alert-circle-outline"}),
    ("last_boot", "Charger last started", {"value_template": "{{ value_json.booted_at }}", "device_class": "timestamp"}),
]  # fmt: skip
_tasks: dict[str, asyncio.Task[Any]] = {}
_pending: set[str] = set()


def _topic(cid: str, what: str) -> str:
    return f"{get_settings().mqtt_topic_prefix}/ocpp/{cid}/{what}"


def discovery(cid: str) -> list[tuple[str, str, bool]]:
    s = get_settings()
    dev = {"identifiers": [f"world_assistant_ocpp_{cid}"], "name": f"Charger {cid}", "manufacturer": "World Assistant (OCPP)"}
    avail = {"availability_topic": _topic(cid, "online"), "payload_available": "online", "payload_not_available": "offline"}
    out = []
    for key, name, extra in SENSORS:
        uid = f"wa_ocpp_{cid.lower()}_{key}"
        cfg = {"name": name, "unique_id": uid, "default_entity_id": f"sensor.wa_ocpp_{key}", "device": dev,
               "state_topic": _topic(cid, "state"), **(avail if key != "last_boot" else {}), **extra}  # fmt: skip
        out.append((f"{s.mqtt_discovery_prefix}/sensor/{uid}/config", json.dumps(cfg), True))
    uid = f"wa_ocpp_{cid.lower()}_connected"
    out.append((f"{s.mqtt_discovery_prefix}/binary_sensor/{uid}/config", json.dumps({
        "name": "Charger connected", "unique_id": uid, "default_entity_id": "binary_sensor.wa_ocpp_connected", "device": dev,
        "state_topic": _topic(cid, "online"), "payload_on": "online", "payload_off": "offline", "device_class": "connectivity"}), True))  # fmt: skip
    uid = f"wa_ocpp_{cid.lower()}_charging"
    out.append((f"{s.mqtt_discovery_prefix}/switch/{uid}/config", json.dumps({
        "name": "Charger charging", "unique_id": uid, "default_entity_id": "switch.wa_ocpp_charging", "device": dev,
        "state_topic": _topic(cid, "state"), "value_template": "{{ 'ON' if value_json.session else 'OFF' }}",
        "command_topic": _topic(cid, "charging/set"), "icon": "mdi:ev-plug-type2", **avail}), True))  # fmt: skip
    uid = f"wa_ocpp_{cid.lower()}_mode"
    out.append((f"{s.mqtt_discovery_prefix}/select/{uid}/config", json.dumps({
        "name": "Charger mode", "unique_id": uid, "default_entity_id": "select.wa_ocpp_mode", "device": dev,
        "state_topic": _topic(cid, "state"), "value_template": "{{ value_json.mode | capitalize }}",
        "command_topic": _topic(cid, "mode/set"), "options": ["Off", "On", "Auto"], "icon": "mdi:ev-station", **avail}), True))  # fmt: skip
    return out


def state(row: dict[str, Any], session: dict[str, Any] | None, mode: str = "auto") -> dict[str, Any]:
    m = row.get("meter") or {}
    session_kwh = None
    if session and session.get("meter_start_wh") is not None:
        last = session.get("last_meter_wh") or m.get("energy_wh")
        if last is not None:
            session_kwh = round(max(0.0, (float(last) - float(session["meter_start_wh"])) / 1000), 3)
    charging = row.get("status") == "Charging"
    return {
        "status": row.get("status") or "unknown",
        "power_kw": round(float(m["power_w"]) / 1000, 2) if charging and m.get("power_w") is not None else 0.0,
        "energy_kwh": round(float(m["energy_wh"]) / 1000, 3) if m.get("energy_wh") is not None else None,
        "session_kwh": session_kwh, "current_a": m.get("current_a") if charging else 0.0,
        "error": row.get("error_code") or "NoError",
        "booted_at": row["booted_at"].isoformat() if row.get("booted_at") else None,
        "session": bool(session and not session.get("stopped_at")),
        "transaction": session.get("transaction_id") if session else None,
        "mode": mode,
    }  # fmt: skip


async def _snapshot(hub: Hub, cid: str) -> tuple[dict[str, Any], bool]:
    from sqlalchemy import text

    async with hub.engine.connect() as conn:
        row = (await conn.execute(text("SELECT * FROM ocpp_charger WHERE id = :c"), {"c": cid})).mappings().first()
        ses = (await conn.execute(text(
            "SELECT * FROM ocpp_session WHERE charger = :c ORDER BY started_at DESC LIMIT 1"), {"c": cid})).mappings().first()  # fmt: skip
    row = dict(row or {})
    return state(row, dict(ses) if ses else None, await hub.mode()), bool(row.get("connected"))


async def _publish(hub: Hub, cid: str, with_discovery: bool) -> None:
    async with hub.engine.connect() as conn:
        from app.modules.ocpp import store

        if not (await store.settings(conn)).get("publish_ha", True):
            return
    conf, _ = await load(hub.engine)
    if not conf.active:
        return
    st, online = await _snapshot(hub, cid)
    msgs = discovery(cid) if with_discovery else []
    msgs += [(_topic(cid, "state"), json.dumps(st), True), (_topic(cid, "online"), "online" if online else "offline", True)]
    await publish(msgs, conf)


def changed(hub: Hub, cid: str, with_discovery: bool = False) -> None:
    """Publish the charger's state soon (changes close together go as one)."""
    if cid in _pending and not with_discovery:
        return
    _pending.add(cid)

    async def go() -> None:
        await asyncio.sleep(0.5)
        _pending.discard(cid)
        try:
            await _publish(hub, cid, with_discovery)
        except Exception:
            log.warning("ocpp: could not publish %s to Home Assistant", cid, exc_info=True)

    asyncio.get_running_loop().create_task(go())


def started(hub: Hub, cid: str) -> None:
    """A charger connected to this process: publish it (with discovery) and listen for its switch."""
    changed(hub, cid, with_discovery=True)
    old = _tasks.get(cid)
    if old is None or old.done():
        _tasks[cid] = asyncio.get_running_loop().create_task(_listen(hub, cid))


async def _listen(hub: Hub, cid: str) -> None:
    """The "charging" switch: ON starts a session, OFF stops it. Only while this process holds the charger."""
    from app.modules.ocpp import protocol

    while cid in hub.links:
        try:
            conf, _ = await load(hub.engine)
            if not conf.active:
                await asyncio.sleep(60)
                continue
            async with client(conf, timeout=15) as c:
                await c.subscribe(_topic(cid, "charging/set"), qos=1)
                await c.subscribe(_topic(cid, "mode/set"), qos=1)
                async for msg in c.messages:
                    if cid not in hub.links:
                        return
                    want = msg.payload.decode(errors="replace").strip().upper() if isinstance(msg.payload, bytes) else str(msg.payload).upper()
                    link = hub.links[cid]
                    if str(msg.topic).endswith("/mode/set"):
                        from app.modules.ocpp import server, store

                        mode = want.lower()
                        if mode in store.MODES:
                            async with hub.engine.begin() as conn:
                                st = await store.settings(conn)
                                await store.save_settings(conn, {**st, "mode": mode})
                            with contextlib.suppress(Exception):
                                await server.apply_mode(hub, cid, mode, "from Home Assistant")
                        continue
                    mode = await hub.mode()
                    if mode != "auto":
                        await hub.keep(cid, events=[("command", f"Home Assistant asked to {want.lower()}: not done, the charger is set to {mode.capitalize()}", {})])
                        changed(hub, cid)
                        continue
                    if hub.redis is not None and await hub.redis.get("wa:planner:acting"):
                        await hub.keep(cid, events=[("command", f"Home Assistant asked to {want.lower()}: not done, World Assistant's planner is choosing when to charge", {})])
                        continue
                    try:
                        if want in ("ON", "OFF"):
                            await hub.want(cid, want == "ON")  # remembered, so a fault in between does not lose it
                        if want == "ON":
                            payload = protocol.command_payload("RemoteStartTransaction", {}, transaction_id=None)
                            await hub.command(cid, "RemoteStartTransaction", payload)
                        elif want == "OFF":
                            payload = protocol.command_payload("RemoteStopTransaction", {}, transaction_id=link.transaction)
                            await hub.command(cid, "RemoteStopTransaction", payload)
                    except ValueError as exc:
                        await hub.keep(cid, events=[("command", f"Home Assistant asked to {want.lower()}: {exc}", {})])
                    changed(hub, cid)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.info("ocpp: Home Assistant switch listener reconnecting", exc_info=True)
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(30)

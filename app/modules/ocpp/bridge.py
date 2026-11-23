"""The charger over MQTT, through the external ocpp2mqtt bridge (https://github.com/gyzod/ocpp2mqtt).

ocpp2mqtt is the OCPP central system the charger connects to; it runs the proven `ocpp` + `websockets` stack (the
same one Home Assistant uses), and speaks to World Assistant over MQTT. World Assistant subscribes to its state
topics and writes them into the same ocpp_charger / ocpp_session / ocpp_event tables (store.py) the built-in server
uses, so the Charger page, logs and Home Assistant entities work unchanged; and it drives the charger by publishing
commands to ocpp2mqtt's command topic.

This runs only while the charger backend is "ocpp2mqtt" (Charger page, switchable live). One api process holds a
Redis lease so nothing is written twice during an update. Topics (base path from settings, e.g. wa/ocpp):

    <base>/<station>/state/<param>      ocpp2mqtt -> us (status, meter, connection, firmware, ...)
    <base>/<station>/cmd                us -> ocpp2mqtt  {"action": "<snake>", "args": {...}}
    <base>/<station>/cmd_result/status  ocpp2mqtt -> us (the charger's answer / an error)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.modules.ocpp import ha, protocol, store
from app.mqtt_config import client, load

log = logging.getLogger("wa.ocpp.mqtt")
LEASE = "wa:ocpp:mqtt:listener"
LEASE_S = 60

# ocpp2mqtt state param -> how to record it. Meter params go into the merged meter JSON; the rest are handled by name.
_METER = {"current_import": "current_a", "voltage": "voltage_v", "power_active_import": "power_w",
          "energy_active_import_register": "energy_wh"}  # fmt: skip
# Charger statuses that mean a session is live (plugged and charging or paused mid-session).
_CHARGING = {"Charging", "SuspendedEV", "SuspendedEVSE"}


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(UTC)


class Bridge:
    """Subscribes to ocpp2mqtt's state topics and records them; one api process at a time (a Redis lease)."""

    def __init__(self) -> None:
        self.engine: Any = None
        self.redis: Any = None
        self.me = uuid.uuid4().hex[:8]
        self.task: asyncio.Task[Any] | None = None
        self.status: dict[str, str] = {}   # station -> last status, to spot transitions and sessions
        self.boot: dict[str, dict[str, Any]] = {}  # station -> accumulated vendor/model/firmware
        self.booted: set[str] = set()

    async def start(self, app: Any) -> None:
        self.engine, self.redis = app.state.engine, app.state.redis
        self.task = asyncio.create_task(self._run(), name="ocpp-mqtt")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task
        with contextlib.suppress(Exception):
            if await self.redis.get(LEASE) == self.me:
                await self.redis.delete(LEASE)

    async def _lease(self) -> bool:
        if await self.redis.get(LEASE) == self.me:
            await self.redis.set(LEASE, self.me, ex=LEASE_S)
            return True
        return bool(await self.redis.set(LEASE, self.me, ex=LEASE_S, nx=True))

    async def _settings(self) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            return await store.settings(conn)

    async def mode(self) -> str:
        """The master-switch mode (ha.changed reads this, like the built-in Hub)."""
        async with self.engine.connect() as conn:
            return str((await store.settings(conn)).get("mode") or "auto")

    async def _run(self) -> None:
        while True:
            try:
                st = await self._settings()
                if st.get("backend") != "ocpp2mqtt":  # the built-in server is in charge; idle
                    await asyncio.sleep(20)
                    continue
                if not await self._lease():
                    await asyncio.sleep(LEASE_S / 2)
                    continue
                conf, _ = await load(self.engine)
                if not conf.active:
                    await asyncio.sleep(30)
                    continue
                await self._listen(conf, str(st.get("mqtt_base") or "wa/ocpp").strip("/"))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.info("ocpp-mqtt: listener reconnecting", exc_info=True)
                await asyncio.sleep(15)

    async def _listen(self, conf: Any, base: str) -> None:
        keep = asyncio.create_task(self._renew())
        try:
            async with client(conf, timeout=15) as c:
                await c.subscribe(f"{base}/+/state/#", qos=0)
                await c.subscribe(f"{base}/+/cmd_result/#", qos=1)
                async for msg in c.messages:
                    with contextlib.suppress(Exception):
                        await self._message(base, str(msg.topic), msg.payload)
        finally:
            keep.cancel()

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(LEASE_S / 3)
            with contextlib.suppress(Exception):
                await self.redis.set(LEASE, self.me, ex=LEASE_S)

    async def _message(self, base: str, topic: str, payload: Any) -> None:
        parts = topic.split("/")
        prefix = base.split("/")
        if parts[: len(prefix)] != prefix or len(parts) < len(prefix) + 3:
            return
        station = parts[len(prefix)]
        section = parts[len(prefix) + 1]
        name = "/".join(parts[len(prefix) + 2 :])
        value = payload.decode(errors="replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
        if section == "cmd_result":
            await self._record(station, events=[("command", f"ocpp2mqtt command result: {value[:200]}", {"raw": value})])
            return
        if section == "state":
            await self._state(station, name, value)

    async def _state(self, cid: str, param: str, value: str) -> None:
        if param in _METER:
            n = _num(value)
            if n is not None:
                await self._record(cid, meter={_METER[param]: n, "at": _now().isoformat()})
            return
        if param == "connection_state":
            up = value.upper() == "CONNECTED"
            await self._record(cid, connected=up, last_seen=_now(),
                               events=[("connected" if up else "disconnected",
                                        "Connected (ocpp2mqtt)" if up else "Disconnected (ocpp2mqtt)", {})])  # fmt: skip
            if up:
                asyncio.create_task(self._apply_after_connect(cid))
            return
        if param == "status":
            await self._status(cid, value)
            return
        if param == "error_code":
            ev = [("fault", f"Fault: {value}", {})] if value and value != "NoError" else []
            await self._record(cid, error_code=value or "NoError", events=ev)
            return
        if param in ("charge_point_vendor", "charge_point_model", "firmware_version"):
            self.boot.setdefault(cid, {})[param] = value
            b = self.boot[cid]
            if cid not in self.booted and b.get("charge_point_vendor"):
                self.booted.add(cid)
                what = " ".join(x for x in (b.get("charge_point_vendor"), b.get("charge_point_model")) if x)
                fw = b.get("firmware_version")
                await self._record(cid, boot=b, booted_at=_now(),
                                   events=[("boot", f"Charger: {what}" + (f", firmware {fw}" if fw else ""), b)])  # fmt: skip
            else:
                await self._record(cid, boot=b)
            return
        if param == "last_seen":
            await self._record(cid, last_seen=_now())

    async def _status(self, cid: str, status: str) -> None:
        prev = self.status.get(cid)
        self.status[cid] = status
        events = [("status", f"Charger: {status}", {})] if status != prev else []
        await self._record(cid, status=status, last_seen=_now(), events=events)
        # Infer a session from the status: a charge begins when it starts charging with none open, and ends when it
        # leaves the charging states. ocpp2mqtt abstracts the OCPP StartTransaction/StopTransaction into status + meter.
        async with self.engine.connect() as conn:
            open_tx = await store.open_transaction(conn, cid)
        if status in _CHARGING and open_tx is None:
            async with self.engine.begin() as conn:
                tid = await store.next_transaction(conn, cid)
                meter = await _last_energy(conn, cid)
                await store.session_start(conn, cid, {"transaction_id": tid, "connector": 1, "id_tag": protocol.ID_TAG,
                                                      "started_at": _now().isoformat(), "meter_start_wh": meter})  # fmt: skip
                await store.event(conn, cid, "session", f"Charging session {tid} started", {"via": "ocpp2mqtt"})
        elif status not in _CHARGING and open_tx is not None:
            async with self.engine.begin() as conn:
                meter = await _last_energy(conn, cid)
                await store.session_stop(conn, cid, {"transaction_id": open_tx, "stopped_at": _now().isoformat(),
                                                    "meter_stop_wh": meter, "reason": "Local"})  # fmt: skip
                await store.event(conn, cid, "session", f"Charging session {open_tx} stopped", {"via": "ocpp2mqtt"})
        if events:
            ha.changed(self, cid)

    async def _apply_after_connect(self, cid: str) -> None:
        await asyncio.sleep(8)
        with contextlib.suppress(Exception):
            async with self.engine.connect() as conn:
                mode = str((await store.settings(conn)).get("mode") or "auto")
            await apply_mode(self.engine, cid, mode, reason="on connecting")

    async def _record(self, cid: str, *, events: list[tuple[str, str, dict[str, Any]]] = (),  # type: ignore[assignment]
                      meter: dict[str, Any] | None = None, **cols: Any) -> None:
        try:
            async with self.engine.begin() as conn:
                if meter is not None:
                    cols["meter"] = meter
                await store.charger(conn, cid, **cols)
                for kind, words, detail in events:
                    await store.event(conn, cid, kind, words, detail)
        except Exception:
            log.exception("ocpp-mqtt: could not record for %s", cid)
        if cols or meter:
            ha.changed(self, cid)


async def _last_energy(conn: Any, cid: str) -> float | None:
    row = (await conn.execute(
        text("SELECT (meter->>'energy_wh')::float FROM ocpp_charger WHERE id = :c"), {"c": cid}
    )).scalar()
    return float(row) if row is not None else None


# --- commands: publish to ocpp2mqtt -------------------------------------------------------------------------------

# WA action -> ocpp2mqtt action name (snake_case) and how to shape its args.
async def publish_command(engine: Any, cid: str, action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Publish one command to ocpp2mqtt's cmd topic. The charger's answer is asynchronous (logged from cmd_result),
    so this reports that the command was sent, not the charger's reply."""
    conf, _ = await load(engine)
    if not conf.active:
        return {"ok": False, "error": "no MQTT broker configured (Settings -> Home Assistant)"}
    async with engine.connect() as conn:
        base = str((await store.settings(conn)).get("mqtt_base") or "wa/ocpp").strip("/")
    body = json.dumps({"action": action, "args": args})
    try:
        async with client(conf, timeout=10) as c:
            await c.publish(f"{base}/{cid}/cmd", body, qos=1)
    except Exception as exc:  # noqa: BLE001 - reported back to the caller
        return {"ok": False, "error": f"MQTT publish failed: {exc}"}
    with contextlib.suppress(Exception):
        async with engine.begin() as conn:
            await store.event(conn, cid, "command", f"Sent {action} via ocpp2mqtt", {"args": args})
    return {"ok": True, "answer": None, "sent": {"action": action, "args": args}, "error": None}


async def apply_mode(engine: Any, cid: str, mode: str, reason: str = "") -> dict[str, Any]:
    """Off makes the charger unavailable (stopping a session first); On makes it available and starts a session; Auto
    makes it available and leaves starting/stopping to the planner. Published to ocpp2mqtt."""
    done: dict[str, Any] = {}
    if mode == "off":
        # The ocpp library's RemoteStopTransaction needs the transaction id, so only stop when a session is open.
        async with engine.connect() as conn:
            tid = await store.open_transaction(conn, cid)
        if tid is not None:
            done["stop"] = await publish_command(engine, cid, "remote_stop_transaction", {"transaction_id": int(tid)})
        done["availability"] = await publish_command(engine, cid, "change_availability",
                                                     {"connector_id": 0, "type": "Inoperative"})  # fmt: skip
    else:
        done["availability"] = await publish_command(engine, cid, "change_availability",
                                                     {"connector_id": 0, "type": "Operative"})  # fmt: skip
        if mode == "on":
            done["start"] = await publish_command(engine, cid, "remote_start_transaction",
                                                 {"connector_id": 1, "id_tag": protocol.ID_TAG})  # fmt: skip
    with contextlib.suppress(Exception):
        async with engine.begin() as conn:
            await store.event(conn, cid, "mode", f"Set to {mode.capitalize()}" + (f" ({reason})" if reason else ""),
                              {"done": done})  # fmt: skip
    return done


async def command(engine: Any, cid: str, action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Translate a World Assistant OCPP command into ocpp2mqtt's snake_case command and publish it."""
    name = _ACTIONS.get(action)
    if name is None:
        return {"ok": False, "error": f"{action} is not available over ocpp2mqtt"}
    return await publish_command(engine, cid, name, _shape(action, args))


_ACTIONS = {"RemoteStartTransaction": "remote_start_transaction", "RemoteStopTransaction": "remote_stop_transaction",
            "ChangeAvailability": "change_availability", "SetChargingProfile": "set_charging_profile",
            "ClearChargingProfile": "clear_charging_profile", "TriggerMessage": "trigger_message",
            "GetConfiguration": "get_configuration", "ChangeConfiguration": "change_configuration",
            "Reset": "reset", "UnlockConnector": "unlock_connector", "ClearCache": "clear_cache"}  # fmt: skip


def _shape(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """OCPP camelCase payload -> ocpp2mqtt snake_case args (the ocpp library's keyword names)."""
    m = {"connectorId": "connector_id", "idTag": "id_tag", "transactionId": "transaction_id",
         "requestedMessage": "requested_message", "csChargingProfiles": "cs_charging_profiles"}  # fmt: skip
    return {m.get(k, k): v for k, v in payload.items()}


BRIDGE = Bridge()


async def startup(app: Any) -> None:
    await BRIDGE.start(app)


async def shutdown() -> None:
    await BRIDGE.stop()

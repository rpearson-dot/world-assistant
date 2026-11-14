"""The air conditioners in Home Assistant, over MQTT, as climate entities (climate.wa_aircon_<unit>): mode (off,
auto, cool, dry, fan_only, heat), target temperature, fan speed and swing, with the indoor and outdoor temperatures.
Home Assistant's changes come back through the api (startup: one listener at a time, by a Redis lease)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from app.config import get_settings
from app.mqtt import publish as mqtt_publish
from app.mqtt_config import client, load

log = logging.getLogger("wa.airstage")
LEASE = "wa:airstage:listener"


def _t(uid: str, what: str) -> str:
    return f"{get_settings().mqtt_topic_prefix}/airstage/{uid}/{what}"


def discovery(u: dict[str, str]) -> tuple[str, str, bool]:
    s = get_settings()
    uid = u["id"]
    cfg = {
        "name": None, "unique_id": f"wa_aircon_{uid}", "default_entity_id": f"climate.wa_aircon_{uid}",
        "device": {"identifiers": [f"world_assistant_aircon_{uid}"], "name": u["name"], "manufacturer": "Fujitsu (via World Assistant)"},
        "modes": ["off", "auto", "cool", "dry", "fan_only", "heat"], "fan_modes": ["auto", "quiet", "low", "medium", "high"],
        "swing_modes": ["on", "off"], "temperature_unit": "C", "precision": 0.5, "temp_step": 0.5, "min_temp": 16, "max_temp": 30,
        "mode_state_topic": _t(uid, "state"), "mode_state_template": "{{ value_json.hvac }}", "mode_command_topic": _t(uid, "set/mode"),
        "temperature_state_topic": _t(uid, "state"), "temperature_state_template": "{{ value_json.target_c }}",
        "temperature_command_topic": _t(uid, "set/target_c"),
        "current_temperature_topic": _t(uid, "state"), "current_temperature_template": "{{ value_json.indoor_c }}",
        "fan_mode_state_topic": _t(uid, "state"), "fan_mode_state_template": "{{ value_json.fan }}", "fan_mode_command_topic": _t(uid, "set/fan"),
        "swing_mode_state_topic": _t(uid, "state"), "swing_mode_state_template": "{{ 'on' if value_json.swing_vertical else 'off' }}",
        "swing_mode_command_topic": _t(uid, "set/swing"),
        "availability_topic": _t(uid, "online"),
    }  # fmt: skip
    return f"{s.mqtt_discovery_prefix}/climate/wa_aircon_{uid}/config", json.dumps(cfg), True


def payload(s: dict[str, Any]) -> dict[str, Any]:
    fan = s.get("fan") or "auto"
    return {**s, "hvac": (s.get("mode") or "auto") if s.get("power") else "off",
            "fan": {"medium_low": "low", "medium_high": "high"}.get(fan, fan)}  # fmt: skip


async def publish(engine: Any, units: list[dict[str, str]], states: dict[str, dict[str, Any]]) -> None:
    try:
        conf, _ = await load(engine)
        if not conf.active:
            return
        msgs = []
        for u in units:
            msgs.append(discovery(u))
            s = states.get(u["id"])
            msgs.append((_t(u["id"], "online"), "online" if s else "offline", True))
            if s:
                msgs.append((_t(u["id"], "state"), json.dumps(payload(s)), True))
        await mqtt_publish(msgs, conf)
    except Exception:
        log.info("airstage: could not publish", exc_info=True)


def command(what: str, value: str) -> dict[str, Any]:
    """A Home Assistant command as a change: set/mode off|auto|cool|dry|fan_only|heat, set/target_c 21.5,
    set/fan auto|quiet|low|medium|high, set/swing on|off."""
    v = value.strip().lower()
    if what == "mode":
        return {"power": False} if v == "off" else {"power": True, "mode": v}
    if what == "target_c":
        return {"target_c": float(v)}
    if what == "fan":
        return {"fan": v}
    if what == "swing":
        return {"swing_vertical": v == "on"}
    raise ValueError(f"unknown command {what}")


class Listener:
    def __init__(self) -> None:
        self.task: asyncio.Task[Any] | None = None
        self.me = uuid.uuid4().hex[:8]

    async def start(self, app: Any) -> None:
        self.task = asyncio.create_task(self._run(app.state.engine, app.state.redis), name="airstage-ha")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()

    async def _run(self, engine: Any, redis: Any) -> None:
        from app.modules.airstage.api import change

        while True:
            try:
                ok = await redis.get(LEASE) == self.me or await redis.set(LEASE, self.me, ex=90, nx=True)
                conf, _ = await load(engine)
                if not ok or not conf.active:
                    await asyncio.sleep(45)
                    continue
                async with client(conf, timeout=15) as c:
                    await c.subscribe(f"{get_settings().mqtt_topic_prefix}/airstage/+/set/+", qos=1)
                    renew = asyncio.create_task(self._renew(redis))
                    try:
                        async for msg in c.messages:
                            parts = str(msg.topic).split("/")
                            uid, what = parts[-3], parts[-1]
                            body = msg.payload.decode(errors="replace") if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload)
                            try:
                                await change(engine, uid, command(what, body), "Home Assistant")
                            except Exception as exc:  # noqa: BLE001 - a refused change is logged, the listener goes on
                                log.warning("airstage: Home Assistant's %s %s for %s not done: %s", what, body, uid, exc)
                    finally:
                        renew.cancel()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.info("airstage: listener reconnecting", exc_info=True)
                await asyncio.sleep(30)

    async def _renew(self, redis: Any) -> None:
        while True:
            await asyncio.sleep(30)
            with contextlib.suppress(Exception):
                await redis.set(LEASE, self.me, ex=90)


LISTENER = Listener()


async def startup(app: Any) -> None:
    await LISTENER.start(app)


async def shutdown() -> None:
    await LISTENER.stop()

"""Publish summaries to MQTT with Home Assistant discovery, so sensors appear without YAML."""

from __future__ import annotations

import json
import logging
import ssl
import time
from typing import Any

import aiomqtt

from app.mqtt_config import MqttConf, client, explain, from_env

log = logging.getLogger("wa.mqtt")

Message = tuple[str, str, bool]  # topic, payload, retain

_BACKOFF_S = 300
_down_until = 0.0
last_error = ""  # why the last publish failed, for the status page ("" after a success)
_last_conf: MqttConf | None = None


def bus_topic(prefix: str, tid: str, name: str) -> str:
    return f"{prefix}/bus/{tid}/{name}"


def bus_sensors(cfg_stops: list[tuple[str, str]]) -> list[tuple[str, str, dict[str, Any]]]:
    """(object name, display name, extra discovery fields) for each sensor of a bus tracker."""
    sensors: list[tuple[str, str, dict[str, Any]]] = [
        ("last_bus", "Last bus to target", {"icon": "mdi:bus-clock"}),
        (
            "hits_today",
            "Buses reaching target today",
            {"icon": "mdi:bus-multiple", "state_class": "measurement"},
        ),
        (
            "pending_slots",
            "Scheduled buses not yet seen",
            {"icon": "mdi:bus-alert", "state_class": "measurement"},
        ),
        ("active_buses", "Buses being followed", {"icon": "mdi:bus", "state_class": "measurement"}),
        ("summary", "Target summary", {"icon": "mdi:text"}),
    ]
    for key, label in cfg_stops:
        sensors.append((f"next_{key}", f"Next bus: {label}",
                        {"icon": "mdi:bus-stop", "unit_of_measurement": "min", "state_class": "measurement"}))  # fmt: skip
    return sensors


def discovery_messages(
    prefix: str, disc_prefix: str, tid: str, cfg_stops: list[tuple[str, str]]
) -> list[Message]:
    device = {"identifiers": [f"world_assistant_bus_{tid}"], "name": f"Bus tracker {tid}",
              "manufacturer": "World Assistant"}  # fmt: skip
    out: list[Message] = []
    for name, label, extra in bus_sensors(cfg_stops):
        uid = f"wa_bus_{tid}_{name}"
        payload = {"name": label, "unique_id": uid, "object_id": uid, "default_entity_id": f"sensor.{uid}",
                   "device": device,
                   "state_topic": bus_topic(prefix, tid, name),
                   "json_attributes_topic": bus_topic(prefix, tid, f"{name}/attributes"), **extra}  # fmt: skip
        out.append((f"{disc_prefix}/sensor/{uid}/config", json.dumps(payload), True))
    return out


def state_messages(prefix: str, tid: str, snapshot: dict[str, dict[str, Any]]) -> list[Message]:
    """snapshot: {sensor name: {"state": value, "attributes": {...}}}"""
    out: list[Message] = []
    for name, item in snapshot.items():
        state = item.get("state")
        out.append((bus_topic(prefix, tid, name), "" if state is None else str(state), True))
        out.append(
            (
                bus_topic(prefix, tid, f"{name}/attributes"),
                json.dumps(item.get("attributes", {}), default=str),
                True,
            )
        )
    return out


async def publish(messages: list[Message], conf: MqttConf | None = None) -> bool:
    """Send messages over one connection. Never raises: MQTT trouble must not stop polling.

    `conf` is the broker from Settings (app/mqtt_config.py); without it, the one in .env.
    """
    global _down_until, last_error, _last_conf
    conf = conf or from_env()
    if conf != _last_conf:  # new settings: try them now rather than after the back-off
        _down_until, _last_conf = 0.0, conf
    if not conf.host:
        last_error = "no broker set (Settings > MQTT, or WA_MQTT_HOST)"
        return False
    if not messages or time.monotonic() < _down_until:
        return False
    try:
        async with client(conf) as c:
            for topic, payload, retain in messages:
                await c.publish(topic, payload, qos=1, retain=retain)
    except (aiomqtt.MqttError, OSError, ssl.SSLError) as exc:
        _down_until = time.monotonic() + _BACKOFF_S
        last_error = explain(exc, conf)
        log.warning("MQTT publish failed (%s); retrying in %d s", type(exc).__name__, _BACKOFF_S)
        return False
    last_error = ""
    return True

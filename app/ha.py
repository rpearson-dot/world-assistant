"""Home Assistant sensors over MQTT discovery: a small model, and the messages it turns into.

Every sensor gets a retained discovery config and a retained state + attributes topic, and an
`expire_after` so a stopped worker shows "unavailable" in Home Assistant, not stale numbers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.mqtt import Message

MANUFACTURER = "World Assistant"


def slug(value: str) -> str:
    """Lower-case letters, digits and underscores only: safe in a topic and an entity id."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "x"


@dataclass(frozen=True)
class Sensor:
    key: str  # unique within its device
    name: str
    state: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)
    icon: str | None = None
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    sensors: list[Sensor]


def state_topic(prefix: str, device: str, key: str) -> str:
    return f"{prefix}/{device}/{key}"


def unique_id(device: str, key: str) -> str:
    return f"wa_{device}_{key}"


def discovery(prefix: str, disc_prefix: str, device: Device, expire_after: int) -> list[Message]:
    info = {"identifiers": [f"world_assistant_{device.id}"], "name": device.name,
            "manufacturer": MANUFACTURER}  # fmt: skip
    out: list[Message] = []
    for s in device.sensors:
        uid = unique_id(device.id, s.key)
        payload: dict[str, Any] = {
            "name": s.name, "unique_id": uid, "device": info,
            # object_id is deprecated in Home Assistant; default_entity_id replaces it.
            "object_id": uid, "default_entity_id": f"sensor.{uid}",
            "state_topic": state_topic(prefix, device.id, s.key),
            "json_attributes_topic": state_topic(prefix, device.id, f"{s.key}/attributes"),
            "expire_after": expire_after,
        }  # fmt: skip
        for k, v in (("icon", s.icon), ("unit_of_measurement", s.unit),
                     ("device_class", s.device_class), ("state_class", s.state_class)):  # fmt: skip
            if v:
                payload[k] = v
        out.append((f"{disc_prefix}/sensor/{uid}/config", json.dumps(payload), True))
    return out


def states(prefix: str, device: Device) -> list[Message]:
    out: list[Message] = []
    for s in device.sensors:
        out.append((state_topic(prefix, device.id, s.key), "" if s.state is None else str(s.state), True))
        out.append((state_topic(prefix, device.id, f"{s.key}/attributes"),
                    json.dumps(s.attributes, default=str), True))  # fmt: skip
    return out


def removals(disc_prefix: str, device_id: str, keys: list[str]) -> list[Message]:
    """Empty retained config messages: Home Assistant deletes the entity."""
    return [(f"{disc_prefix}/sensor/{unique_id(device_id, k)}/config", "", True) for k in keys]

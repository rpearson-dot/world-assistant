"""The Home Assistant names World Assistant's files are written with, and how a file is written with yours. Pure.

The shipped files (homeassistant/) use one installation's names: an inverter called office_inverter, an OCPP charger
called charger, an OVMS car HV66BZD, Forecast.Solar's first array. Each download and Samba copy puts in the names
saved on the /ha page (found by "Find my setup", app.ha_discover, or typed in).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Name:
    key: str
    label: str
    default: str
    kind: str  # token: a word inside entity ids | charger: an OCPP charger's entity prefix | entity: a whole entity id
    hint: str


NAMES: tuple[Name, ...] = (
    Name("inverter", "Inverter", "office_inverter", "token",
         "the part between sensor. and _battery_soc in your inverter's battery sensor"),
    Name("ocpp_charger", "Car charger (OCPP)", "charger", "charger",
         "the charger's name in the OCPP integration: switch.<name>_charge_control"),
    Name("ovms_vehicle", "Car (OVMS vehicle id)", "hv66bzd", "token", "the OVMS vehicle id in lower case"),
    Name("solar_today", "Solar forecast today", "sensor.energy_production_today", "entity",
         "Forecast.Solar's estimated energy production today"),
    Name("solar_tomorrow", "Solar forecast tomorrow", "sensor.energy_production_tomorrow", "entity",
         "Forecast.Solar's estimated energy production tomorrow"),
    Name("solar_remaining", "Solar forecast rest of today", "sensor.energy_production_today_remaining", "entity",
         "Forecast.Solar's estimated energy production remaining today"),
    Name("solar_now", "Solar forecast power now", "sensor.power_production_now", "entity",
         "Forecast.Solar's estimated power production now"),
)  # fmt: skip
BY_KEY = {n.key: n for n in NAMES}
DEFAULTS = {n.key: n.default for n in NAMES}
TOKEN = re.compile(r"^[a-z0-9_]{1,60}$")
ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]{1,200}$")
CHARGER_DOMAINS = r"(sensor|switch|number|button|binary_sensor|select)"


def clean(key: str, raw: str) -> str:
    """A name as typed (or pasted as an entity id), checked; ValueError says what is wrong."""
    n = BY_KEY.get(key)
    if n is None:
        raise ValueError(f"no such name: {key}")
    v = raw.strip().lower()
    if n.kind == "entity":
        if not ENTITY.match(v):
            raise ValueError(f"{n.label}: an entity id, like {n.default}")
        return v
    if "." in v:  # an entity id pasted: take the part that is the name
        v = v.split(".", 1)[1]
        if key == "inverter":
            v = re.sub(r"_(battery_soc|battery_state_of_charge|time_slot_\d_capacity)$", "", v)
        elif key == "ocpp_charger":
            v = re.sub(r"_(charge_control|status_connector)$", "", v)
    if not TOKEN.match(v):
        raise ValueError(f"{n.label}: lower case letters, digits and _, like {n.default}")
    return v


def merged(saved: dict[str, str] | None) -> dict[str, str]:
    out = dict(DEFAULTS)
    for k, v in (saved or {}).items():
        if k in out and isinstance(v, str) and v:
            out[k] = v
    return out


def uses(body: str) -> list[str]:
    """Which names a file is written with."""
    return [n.key for n in NAMES if _pattern(n).search(body)]


def _pattern(n: Name) -> re.Pattern[str]:
    if n.kind == "entity":
        return re.compile(re.escape(n.default) + r"(?![a-z0-9_])")
    if n.kind == "charger":
        return re.compile(r"\b" + CHARGER_DOMAINS + r"\." + re.escape(n.default) + r"_")
    return re.compile(re.escape(n.default))


def render(body: str, names: dict[str, str]) -> str:
    """The file written with these names (defaults for any not given)."""
    for n in NAMES:
        v = names.get(n.key) or n.default
        if v == n.default:
            continue
        if n.kind == "charger":
            body = _pattern(n).sub(lambda m, v=v: f"{m.group(1)}.{v}_", body)
        else:
            body = _pattern(n).sub(v.replace("\\", "\\\\"), body)
    return body

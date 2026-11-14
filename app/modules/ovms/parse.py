"""Reading what an OVMS module publishes over MQTT, and what in it is a warning. Pure, so it is tested on its own.

OVMS v3 publishes under its topic prefix (by default ovms/<user>/<vehicle id>/):
- metric/<name with / for .>  one value, as text ("yes"/"no" for true and false), e.g. metric/v/b/soc -> "81"
- notify/<type>/<subtype>     a notification: type info, alert, error or data
- event                        the name of an event as it happens
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Metrics whose history is kept (only when they change, and no more than once a minute while they move)
KEEP = ("v.b.soc", "v.b.soh", "v.b.range.est", "v.b.range.ideal", "v.b.12v.voltage", "v.b.temp", "v.b.power",
        "v.c.state", "v.c.mode", "v.c.kwh", "v.c.charging", "v.c.pilot", "v.e.on", "v.e.locked", "v.e.handbrake",
        "v.e.alarm", "v.e.parktime", "v.p.odometer", "v.p.latitude", "v.p.longitude", "v.p.speed", "v.m.temp",
        "m.net.sq", "m.net.provider")  # fmt: skip
NOTIFY_KINDS = ("alert", "error", "info")


def topic(t: str, prefix: str) -> tuple[str, str, str] | None:
    """(vehicle, kind, name) from a topic under the prefix ("ovms/rob/HV66BZD/"): kind metric, notify or event.
    The vehicle is the last part of the prefix as the car publishes it (its vehicle id)."""
    parts = t.split("/")
    pre = [p for p in prefix.strip("/").split("/") if p]
    if len(parts) <= len(pre):
        return None
    for want, got in zip(pre, parts, strict=False):
        if want not in ("+", got):
            return None
    vehicle, rest = parts[len(pre) - 1], parts[len(pre):]
    if not rest:
        return None
    if rest[0] == "metric" and len(rest) > 1:
        return vehicle, "metric", ".".join(rest[1:])
    if rest[0] == "notify" and len(rest) > 1:
        return vehicle, "notify", "/".join(rest[1:])
    if rest[0] == "event":
        return vehicle, "event", ""
    return None


def truthy(v: str | None) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("yes", "true", "1", "on"):
        return True
    if s in ("no", "false", "0", "off", ""):
        return False
    return None


def num(v: str | None) -> float | None:
    try:
        return float(str(v).split()[0]) if v not in (None, "") else None
    except (ValueError, IndexError):
        return None


def warnings(m: dict[str, tuple[str | None, datetime]], now: datetime, *, quiet_h: float = 6) -> list[dict[str, Any]]:
    """What in the car's metrics needs attention. m: metric -> (value, when it last came)."""
    v = {k: x[0] for k, x in m.items()}
    out: list[dict[str, Any]] = []

    def add(key: str, words: str, level: str = "warning") -> None:
        out.append({"key": key, "text": words, "level": level})

    parked = truthy(v.get("v.e.on")) is False
    if truthy(v.get("v.b.12v.voltage.alert")):
        volts = num(v.get("v.b.12v.voltage"))
        add("12v", "12 V battery low" + (f" ({volts:.2f} V)" if volts else ""))
    if truthy(v.get("v.e.alarm")):
        add("alarm", "The car's alarm is sounding", "alert")
    tyres = [k for k in v if k.startswith("v.t.alert") and (num(v[k]) or 0) > 0]
    if tyres:
        add("tyres", "Tyre warning")
    if parked:
        doors = {"v.d.fl": "front left door", "v.d.fr": "front right door", "v.d.rl": "rear left door",
                 "v.d.rr": "rear right door", "v.d.hood": "bonnet", "v.d.trunk": "boot"}  # fmt: skip
        open_ = [n for k, n in doors.items() if truthy(v.get(k))]
        if open_:
            add("doors", "Open while parked: " + ", ".join(open_))
        if truthy(v.get("v.e.locked")) is False:
            add("unlocked", "Unlocked while parked")
        if truthy(v.get("v.e.handbrake")) is False and (num(v.get("v.p.speed")) or 0) < 1:
            add("handbrake", "Handbrake off while parked")
    soc = num(v.get("v.b.soc"))
    if soc is not None and soc < 15:
        add("soc", f"Battery at {soc:.0f}%")
    newest = max((x[1] for x in m.values()), default=None)
    if newest is not None and now - newest > timedelta(hours=quiet_h):
        add("quiet", f"OVMS has said nothing since {newest.astimezone().strftime('%H:%M %d %b')}", "info")
    return out


def summary(m: dict[str, tuple[str | None, datetime]]) -> dict[str, Any]:
    v = {k: x[0] for k, x in m.items()}
    return {
        "soc": num(v.get("v.b.soc")), "soh": num(v.get("v.b.soh")), "range_km": num(v.get("v.b.range.est")),
        "volts_12v": num(v.get("v.b.12v.voltage")), "charging": truthy(v.get("v.c.charging")),
        "charge_state": v.get("v.c.state"), "on": truthy(v.get("v.e.on")), "locked": truthy(v.get("v.e.locked")),
        "odometer_km": num(v.get("v.p.odometer")), "lat": num(v.get("v.p.latitude")), "lon": num(v.get("v.p.longitude")),
        "signal": num(v.get("m.net.sq")), "network": v.get("m.net.provider"),
        "last": max((x[1] for x in m.values()), default=None),
    }  # fmt: skip

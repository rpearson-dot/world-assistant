"""Reading what an OVMS module publishes over MQTT, and what in it is a warning. Pure, so it is tested on its own.

OVMS v3 publishes under its topic prefix (by default ovms/<user>/<vehicle id>/):
- metric/<name with / for .>  one value, as text ("yes"/"no" for true and false), e.g. metric/v/b/soc -> "81"
- notify/<type>/<subtype>     a notification: type info, alert, error or data
- event                        the name of an event as it happens
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Metrics whose history is kept (only when they change, and no more than once a minute while they move). Kept wide so
# the car page can chart most of what the module reports; booleans are logged on every change, numbers at most once a
# minute while they move (see listener._flush).
KEEP = (
    # battery
    "v.b.soc", "v.b.soh", "v.b.range.est", "v.b.range.ideal", "v.b.12v.voltage", "v.b.temp", "v.b.power",
    "v.b.current", "v.b.voltage", "v.b.energy.used", "v.b.energy.recd", "v.b.consumption", "v.b.cac", "v.b.health",
    # charging
    "v.c.state", "v.c.mode", "v.c.kwh", "v.c.charging", "v.c.pilot", "v.c.power", "v.c.current", "v.c.voltage",
    "v.c.efficiency", "v.c.temp", "v.c.climit", "v.c.duration.full", "v.c.limit.soc", "v.c.limit.range",
    # environment, doors, motion
    "v.e.on", "v.e.locked", "v.e.handbrake", "v.e.alarm", "v.e.parktime", "v.e.cabintemp", "v.e.temp", "v.e.throttle",
    "v.m.temp", "v.m.rpm", "v.i.temp",
    # position
    "v.p.odometer", "v.p.latitude", "v.p.longitude", "v.p.speed", "v.p.altitude", "v.p.gpsspeed", "v.p.trip",
    "v.p.satcount", "v.p.direction",
    # tyres
    "v.tp.fl.p", "v.tp.fr.p", "v.tp.rl.p", "v.tp.rr.p", "v.tp.fl.t", "v.tp.fr.t", "v.tp.rl.t", "v.tp.rr.t",
    # module / network, and the Leaf's own dashboard readings
    "m.net.sq", "m.net.provider", "xnl.v.b.soc.instrument", "xnl.v.b.range.instrument",
)  # fmt: skip
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


def base_prefix(t: str, prefix: str) -> str | None:
    """The concrete topic prefix a specific car publishes under ("ovms/rob/HV66BZD/"), worked out from one of its
    topics and the configured prefix (which may use + wildcards). OVMS commands are published beneath this, at
    <base>client/<app>/command/<id>, so it is remembered per car to send controls."""
    parts = t.split("/")
    pre = [p for p in prefix.strip("/").split("/") if p]
    if len(parts) <= len(pre):
        return None
    for want, got in zip(pre, parts, strict=False):
        if want not in ("+", got):
            return None
    return "/".join(parts[: len(pre)]) + "/"


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
    tyres = [num(v.get(k)) for k in ("v.tp.fl.p", "v.tp.fr.p", "v.tp.rl.p", "v.tp.rr.p")]
    tyres = [t for t in tyres if t is not None]
    return {
        "soc": num(v.get("v.b.soc")), "soh": num(v.get("v.b.soh")), "range_km": num(v.get("v.b.range.est")),
        "range_ideal_km": num(v.get("v.b.range.ideal")), "volts_12v": num(v.get("v.b.12v.voltage")),
        "battery_temp": num(v.get("v.b.temp")), "charging": truthy(v.get("v.c.charging")),
        "charge_state": v.get("v.c.state"), "charge_mode": v.get("v.c.mode"), "charge_power_kw": num(v.get("v.c.power")),
        "charge_limit_soc": num(v.get("v.c.limit.soc")), "charge_eta_min": num(v.get("v.c.duration.full")),
        "on": truthy(v.get("v.e.on")), "locked": truthy(v.get("v.e.locked")),
        "cabin_temp": num(v.get("v.e.cabintemp")), "ambient_temp": num(v.get("v.e.temp")),
        "tyres_bar": (round(min(tyres), 2) if tyres else None),
        "odometer_km": num(v.get("v.p.odometer")), "speed_kmh": num(v.get("v.p.speed")),
        "lat": num(v.get("v.p.latitude")), "lon": num(v.get("v.p.longitude")),
        "signal": num(v.get("m.net.sq")), "network": v.get("m.net.provider"),
        "last": max((x[1] for x in m.values()), default=None),
    }  # fmt: skip

"""Publish World Assistant data to Home Assistant: pollen, fuel, trains and buses, roads, events.

The worker reads the service's own API (so the numbers match the web pages exactly), turns each
answer into sensors (app/ha.py) and sends them over MQTT. One domain failing never stops the others.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from app import ha, mqtt
from app.config import get_settings
from app.ha import Device, Sensor, slug

log = logging.getLogger("wa.ha")

FUELS = ("E10", "B7_STANDARD")
FUEL_NAMES = {"E10": "Petrol (E10)", "B7_STANDARD": "Diesel"}


def pollen_device(data: dict[str, Any]) -> Device:
    dates = data.get("dates") or []
    risk = data.get("allergy_risk") or []
    risk_level = data.get("allergy_risk_level") or []
    risk_label = data.get("allergy_risk_label") or []
    place = data.get("place")
    sensors = [
        Sensor("allergy_risk", "Allergy risk", risk[0] if risk else None, icon="mdi:flower-pollen",
               unit="/10", state_class="measurement",
               attributes={"place": place, "label": risk_label[0] if risk_label else None,
                           "dates": dates, "forecast": risk, "labels": risk_label,
                           "source": data.get("source")}),
        Sensor("allergy_risk_label", "Allergy risk label", risk_label[0] if risk_label else None,
               icon="mdi:flower-pollen-outline", attributes={"levels": risk_level, "dates": dates}),
    ]  # fmt: skip
    for a in data.get("allergens") or []:
        levels = a.get("levels") or []
        labels = a.get("labels") or []
        key = f"pollen_{slug(a.get('id') or a.get('name') or 'x')}"
        sensors.append(Sensor(key, f"Pollen: {a.get('name') or a.get('id')}",
                              levels[0] if levels else None, icon="mdi:grass", state_class="measurement",
                              attributes={"label": labels[0] if labels else None, "levels": levels,
                                          "labels": labels, "dates": dates}))  # fmt: skip
    return Device("pollen", "Pollen", sensors)


def fuel_device(results: dict[str, dict[str, Any]]) -> Device:
    sensors: list[Sensor] = []
    for fuel, data in results.items():
        best = data.get("cheapest_overall") or {}
        stations = [
            {"station": s.get("trading_name"), "brand": s.get("brand_name"), "pence": s.get("pence"),
             "distance_km": s.get("distance_km"), "postcode": s.get("postcode")}
            for s in (data.get("stations") or [])[:5]
        ]  # fmt: skip
        sensors.append(Sensor(
            f"cheapest_{slug(fuel)}", f"Cheapest {FUEL_NAMES.get(fuel, fuel)}", best.get("pence"),
            icon="mdi:gas-station", unit="p", state_class="measurement",
            attributes={"station": best.get("trading_name"), "brand": best.get("brand_name"),
                        "postcode": best.get("postcode"), "distance_km": best.get("distance_km"),
                        "price_age_hours": best.get("price_age_hours"), "nearest": stations},
        ))  # fmt: skip
    return Device("fuel", "Fuel prices", sensors)


def trains_device(boards: list[tuple[dict[str, Any], dict[str, Any]]]) -> Device:
    """boards: (board row, its departures answer)."""
    sensors: list[Sensor] = []
    for board, data in boards:
        bid = slug(str(board.get("id") or board.get("name")))
        deps = data.get("departures") or []
        rows = [
            {"destination": d.get("destination"), "line": d.get("line"), "platform": d.get("platform"),
             "minutes": d.get("minutes"), "status": d.get("status"), "delay_minutes": d.get("delay_minutes")}
            for d in deps
        ]  # fmt: skip
        first = deps[0] if deps else {}
        name = board.get("name") or bid
        sensors.append(Sensor(
            f"{bid}_next", f"{name}: next departure", first.get("minutes"), icon="mdi:train",
            unit="min", state_class="measurement",
            attributes={"destination": first.get("destination"), "platform": first.get("platform"),
                        "status": first.get("status"), "stale": data.get("stale"),
                        "as_of": data.get("as_of"), "departures": rows},
        ))  # fmt: skip
        bad = [r for r in rows if (r["status"] or "").lower() not in ("", "ontime", "on time")
               or (r["delay_minutes"] or 0) >= 5]  # fmt: skip
        sensors.append(Sensor(f"{bid}_disrupted", f"{name}: delayed or cancelled", len(bad),
                              icon="mdi:train-variant", state_class="measurement",
                              attributes={"services": bad}))  # fmt: skip
    return Device("trains", "Trains and buses", sensors)


def roads_device(status: dict[str, Any]) -> Device:
    corridors = [c for c in status.get("corridors") or [] if c.get("enabled", True)]
    sensors = [
        Sensor(f"corridor_{slug(str(c.get('id')))}", f"{c.get('name') or c.get('id')}: active works",
               c.get("active_events") or 0, icon="mdi:road-variant", state_class="measurement",
               attributes={"live_events": c.get("live_events") or 0, "corridor": c.get("id")})
        for c in corridors
    ]  # fmt: skip
    live = sum(int(r.get("n") or 0) for r in status.get("events_live") or [])
    sensors.append(Sensor("live_events", "Live road events", live, icon="mdi:traffic-cone",
                          state_class="measurement"))  # fmt: skip
    return Device("roads", "Roads", sensors)


def events_device(data: dict[str, Any]) -> Device:
    events = data.get("events") or []
    first = events[0] if events else {}
    rows = [{"name": e.get("name"), "date": e.get("date"), "venue": e.get("venue"),
             "type": e.get("type"), "source": e.get("source")} for e in events[:5]]  # fmt: skip
    return Device("events", "Events nearby", [
        Sensor("upcoming", "Events coming up", data.get("count", len(events)), icon="mdi:calendar-star",
               state_class="measurement", attributes={"days": data.get("days"), "next": rows,
                                                      "place": data.get("place")}),
        Sensor("next_event", "Next event", first.get("name"), icon="mdi:ticket",
               attributes={"date": first.get("date"), "venue": first.get("venue")}),
    ])  # fmt: skip


async def _get(c: httpx.AsyncClient, path: str, **params: Any) -> Any:
    r = await c.get(path, params=params or None)
    r.raise_for_status()
    return r.json()


async def collect(c: httpx.AsyncClient) -> list[Device]:
    """Ask the API for each domain. A domain that fails or isn't set up is skipped."""
    devices: list[Device] = []

    async def attempt(name: str, build: Any) -> None:
        try:
            devices.append(await build())
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.info("home assistant: skipping %s (%s: %s)", name, type(exc).__name__, exc)

    async def pollen() -> Device:
        return pollen_device(await _get(c, "/v1/pollen/near"))

    async def fuel() -> Device:
        return fuel_device({f: await _get(c, "/v1/fuel/near", fuel=f, limit=5) for f in FUELS})

    async def trains() -> Device:
        pairs = []
        for b in await _get(c, "/v1/tfl/boards"):
            try:
                pairs.append((b, await _get(c, f"/v1/tfl/boards/{b['id']}/departures")))
            except httpx.HTTPError as exc:  # one dead board must not hide the rest
                log.info("home assistant: board %s skipped (%s)", b.get("id"), type(exc).__name__)
        return trains_device(pairs)

    async def roads() -> Device:
        return roads_device(await _get(c, "/v1/status"))

    async def events() -> Device:
        return events_device(await _get(c, "/v1/events/near"))

    for name, build in (("pollen", pollen), ("fuel", fuel), ("trains", trains),
                        ("roads", roads), ("events", events)):  # fmt: skip
        await attempt(name, build)
    return devices


def messages(devices: list[Device], *, with_discovery: bool, expire_after: int) -> list[mqtt.Message]:
    s = get_settings()
    out: list[mqtt.Message] = []
    for d in devices:
        if with_discovery:
            out += ha.discovery(s.mqtt_topic_prefix, s.mqtt_discovery_prefix, d, expire_after)
        out += ha.states(s.mqtt_topic_prefix, d)
    return out


def _fingerprint(devices: list[Device]) -> str:
    shape = [(d.id, d.name, [(x.key, x.name, x.unit, x.icon) for x in d.sensors]) for d in devices]
    return hashlib.sha256(repr(shape).encode()).hexdigest()


async def ha_sync(ctx: dict[str, Any]) -> int:
    """Worker job: publish every domain. Discovery goes out on start and whenever the sensors change."""
    s = get_settings()
    if not (s.mqtt_enabled and s.ha_publish):
        return 0
    async with httpx.AsyncClient(
        base_url=s.ha_api_url, timeout=30, headers={"Authorization": f"Bearer {s.api_token}"}
    ) as c:
        devices = await collect(c)
    if not devices:
        return 0
    fp = _fingerprint(devices)
    first = ctx.get("ha_fingerprint") != fp
    expire = max(1, s.ha_interval_min) * 60 * 3
    ok = await mqtt.publish(messages(devices, with_discovery=first, expire_after=expire))
    if ok:
        ctx["ha_fingerprint"] = fp
    return sum(len(d.sensors) for d in devices) if ok else 0

"""Publish World Assistant data to Home Assistant: pollen, fuel, trains and buses, and roads.

Nationwide data (pollen, fuel, roads) goes out with positions and Home Assistant filters it for
whoever is looking; nothing here knows a location. Events are still fetched by Home Assistant for
each person (scripts/ha-setup.py).

The worker reads the service's own API (so the numbers match the web pages exactly), turns each
answer into sensors (app/ha.py) and sends them over MQTT. One domain failing never stops the others.
"""

from __future__ import annotations

import hashlib
import logging
import math
import statistics
from typing import Any

import httpx

from app import ha, mqtt
from app.config import get_settings
from app.ha import Device, Sensor, slug
from app.pollen_map import render_svg

log = logging.getLogger("wa.ha")

FUELS = ("E10", "B7_STANDARD")
FUEL_NAMES = {"E10": "Petrol (E10)", "B7_STANDARD": "Diesel"}


TILE_DEG = 0.5  # fuel stations are grouped into tiles of this size so each sensor stays small


def _tile(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / TILE_DEG), math.floor(lon / TILE_DEG)


def fuel_device(results: dict[str, list[dict[str, Any]]]) -> Device:
    """Every station with a price, nationwide. One sensor per fuel and map tile (state: how many
    stations), with the stations and their coordinates in attributes, plus a national summary per
    fuel. Home Assistant filters by its own zones; nothing here knows where you are."""
    sensors: list[Sensor] = []
    for fuel, stations in results.items():
        label = FUEL_NAMES.get(fuel, fuel)
        prices = sorted(s["pence"] for s in stations if s.get("pence") is not None)
        cheapest = min(stations, key=lambda s: s["pence"], default=None)
        sensors.append(Sensor(
            f"national_{slug(fuel)}", f"{label}: national median", statistics.median(prices) if prices else None,
            icon="mdi:gas-station", unit="p", state_class="measurement",
            attributes={"stations": len(stations), "cheapest_pence": cheapest and cheapest["pence"],
                        "cheapest_station": cheapest and cheapest["name"],
                        "cheapest_postcode": cheapest and cheapest["postcode"]},
        ))  # fmt: skip
        tiles: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for st in stations:
            tiles.setdefault(_tile(st["lat"], st["lon"]), []).append(st)
        for (ti, tj), rows in sorted(tiles.items()):
            rows.sort(key=lambda r: r["pence"])
            sensors.append(Sensor(
                f"tile_{slug(fuel)}_{ti}_{tj}".replace("-", "m"), f"{label} tile {ti}/{tj}", len(rows),
                icon="mdi:map-marker-radius", state_class="measurement",
                attributes={"fuel": fuel, "lat_from": ti * TILE_DEG, "lon_from": tj * TILE_DEG,
                            "size_deg": TILE_DEG, "stations": rows},
            ))  # fmt: skip
    return Device("fuel", "Fuel prices", sensors)


POLLEN_TILE_DEG = 2.0  # pollen lattice points are about a degree apart; a tile holds a few


def pollen_device(points: list[dict[str, Any]]) -> Device:
    """The nationwide pollen forecast, in tiles of lattice points with positions, so Home Assistant
    picks the point nearest each viewer. Nothing here knows where anyone is."""
    tiles: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for p in points:
        row = {
            "lat": p["lat"], "lon": p["lon"], "dates": p.get("dates"), "risk": p.get("allergy_risk"),
            "label": p.get("allergy_risk_label"), "changed_at": p.get("changed_at"),
            "allergens": [{"name": a.get("name"), "levels": a.get("levels")} for a in p.get("allergens") or []],
        }  # fmt: skip
        tiles.setdefault((math.floor(p["lat"] / POLLEN_TILE_DEG), math.floor(p["lon"] / POLLEN_TILE_DEG)), []).append(row)
    sensors = [
        Sensor(f"tile_{ti}_{tj}".replace("-", "m"), f"Pollen tile {ti}/{tj}", len(rows), icon="mdi:flower-pollen",
               state_class="measurement", attributes={"points": rows, "size_deg": POLLEN_TILE_DEG})
        for (ti, tj), rows in sorted(tiles.items())
    ]  # fmt: skip
    images = []
    if points:
        images.append(ha.Image("map", "Pollen map", render_svg(points).encode("utf-8")))
    return Device("pollen", "Pollen", sensors, images)


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


def roads_device(status: dict[str, Any], events: list[dict[str, Any]] | None = None) -> Device:
    """Corridor counts, plus every live closure and speed restriction nationwide in map tiles
    (with positions) so Home Assistant can show the ones near its own zones."""
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
    tiles: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for e in events or []:
        if e.get("lat") is not None and e.get("lon") is not None:
            tiles.setdefault(_tile(e["lat"], e["lon"]), []).append(e)
    for (ti, tj), rows in sorted(tiles.items()):
        sensors.append(Sensor(
            f"tile_{ti}_{tj}".replace("-", "m"), f"Road events tile {ti}/{tj}", len(rows),
            icon="mdi:map-marker-alert", state_class="measurement",
            attributes={"lat_from": ti * TILE_DEG, "lon_from": tj * TILE_DEG, "size_deg": TILE_DEG,
                        "events": rows},
        ))  # fmt: skip
    return Device("roads", "Roads", sensors)


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
        return pollen_device((await _get(c, "/v1/pollen/grid"))["points"])

    async def fuel() -> Device:
        return fuel_device({f: (await _get(c, "/v1/fuel/all", fuel=f))["stations"] for f in FUELS})

    async def trains() -> Device:
        pairs = []
        for b in await _get(c, "/v1/tfl/boards"):
            try:
                pairs.append((b, await _get(c, f"/v1/tfl/boards/{b['id']}/departures")))
            except httpx.HTTPError as exc:  # one dead board must not hide the rest
                log.info("home assistant: board %s skipped (%s)", b.get("id"), type(exc).__name__)
        return trains_device(pairs)

    async def roads() -> Device:
        status = await _get(c, "/v1/status")
        try:
            events = (await _get(c, "/v1/status/events/map"))["events"]
        except httpx.HTTPError as exc:  # the counts are still worth publishing
            log.info("home assistant: road event positions skipped (%s)", type(exc).__name__)
            events = []
        return roads_device(status, events)

    for name, build in (("pollen", pollen), ("fuel", fuel), ("trains", trains), ("roads", roads)):
        await attempt(name, build)
    return devices


def messages(
    devices: list[Device], *, with_discovery: bool, expire_after: int, sent_images: dict[str, str] | None = None
) -> list[mqtt.Message]:
    """Everything to publish. A picture is only sent when it differs from the last one sent
    (sent_images maps topic to a hash, and is updated here), since it is large and retained."""
    s = get_settings()
    out: list[mqtt.Message] = []
    for d in devices:
        if with_discovery:
            out += ha.discovery(s.mqtt_topic_prefix, s.mqtt_discovery_prefix, d, expire_after)
            out += ha.image_discovery(s.mqtt_topic_prefix, s.mqtt_discovery_prefix, d)
        out += ha.states(s.mqtt_topic_prefix, d)
        for topic, payload, retain in ha.image_states(s.mqtt_topic_prefix, d):
            digest = hashlib.sha256(payload.encode()).hexdigest()
            if sent_images is None or sent_images.get(topic) != digest:
                out.append((topic, payload, retain))
                if sent_images is not None:
                    sent_images[topic] = digest
    return out


def _fingerprint(devices: list[Device]) -> str:
    shape = [(d.id, d.name, [(x.key, x.name, x.unit, x.icon) for x in d.sensors],
              [(i.key, i.name) for i in d.images]) for d in devices]
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
    sent: dict[str, str] = ctx.setdefault("ha_images", {})
    before = dict(sent)
    ok = await mqtt.publish(messages(devices, with_discovery=first, expire_after=expire, sent_images=sent))
    if ok:
        ctx["ha_fingerprint"] = fp
    else:
        ctx["ha_images"] = before  # not sent: try again next time
    return sum(len(d.sensors) for d in devices) if ok else 0

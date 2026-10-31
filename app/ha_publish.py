"""Publish World Assistant data to Home Assistant: pollen, fuel, trains and buses, roads, and events.

Nationwide data (pollen, fuel, roads) goes out with positions and Home Assistant filters it for
whoever is looking; nothing here knows a location. Events for Home Assistant's home zone are fetched by Home Assistant
(homeassistant/packages/world_assistant_events.yaml); the extra places it asks for (app/ha_locations.py)
are published here, one sensor each.

The worker reads the service's own API (so the numbers match the web pages exactly), turns each
answer into sensors (app/ha.py) and sends them over MQTT. One domain failing never stops the others.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics
from datetime import UTC, datetime
from typing import Any

import httpx

from app import ha, mqtt
from app.config import get_settings
from app.energy.ha import RETIRED as ENERGY_RETIRED
from app.energy.ha import energy_device
from app.ha import Device, Sensor, slug
from app.mqtt_config import from_env, load
from app.pollen_map import render_svg

log = logging.getLogger("wa.ha")

FUELS = ("E10", "B7_STANDARD")
FUEL_NAMES = {"E10": "Petrol (E10)", "B7_STANDARD": "Diesel"}
FRESH_H = 48.0  # the national median uses only prices this recent; older ones are still listed, with their age
STATION_KEYS = ("name", "brand", "postcode", "lat", "lon", "pence", "age_h")  # what a tile keeps of a station


TILE_DEG = 0.5  # fuel stations are grouped into tiles of this size so each sensor stays small


MAX_ATTR_BYTES = 12000  # Home Assistant's recorder drops attributes over 16384 bytes; stay well under


def _chunks(rows: list[dict[str, Any]], limit: int = MAX_ATTR_BYTES) -> list[list[dict[str, Any]]]:
    """Split a tile's rows so each part's JSON stays under `limit` bytes (one part when it already does)."""
    out: list[list[dict[str, Any]]] = [[]]
    size = 0
    for r in rows:
        n = len(json.dumps(r, default=str)) + 2
        if out[-1] and size + n > limit:
            out.append([])
            size = 0
        out[-1].append(r)
        size += n
    return out


def _tile(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / TILE_DEG), math.floor(lon / TILE_DEG)


def fuel_device(results: dict[str, list[dict[str, Any]]]) -> Device:
    """Every station with a price, nationwide. One sensor per fuel and map tile (state: how many
    stations), with the stations and their coordinates in attributes, plus a national summary per
    fuel. Home Assistant filters by its own zones; nothing here knows where you are."""
    sensors: list[Sensor] = []
    for fuel, stations in results.items():
        label = FUEL_NAMES.get(fuel, fuel)
        fresh = [s for s in stations if s.get("age_h") is None or s["age_h"] <= FRESH_H]
        prices = sorted(s["pence"] for s in fresh if s.get("pence") is not None)
        cheapest = min(fresh, key=lambda s: s["pence"], default=None)
        sensors.append(Sensor(
            f"national_{slug(fuel)}", f"{label}: national median", statistics.median(prices) if prices else None,
            icon="mdi:gas-station", unit="p", state_class="measurement",
            attributes={"stations": len(fresh), "listed": len(stations), "cheapest_pence": cheapest and cheapest["pence"],
                        "cheapest_station": cheapest and cheapest["name"],
                        "cheapest_postcode": cheapest and cheapest["postcode"]},
        ))  # fmt: skip
        tiles: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for st in stations:
            tiles.setdefault(_tile(st["lat"], st["lon"]), []).append({k: st.get(k) for k in STATION_KEYS})
        for (ti, tj), rows in sorted(tiles.items()):
            rows.sort(key=lambda r: r["pence"])
            for part, chunk in enumerate(_chunks(rows)):
                sensors.append(Sensor(
                    f"tile_{slug(fuel)}_{ti}_{tj}".replace("-", "m") + (f"_p{part + 1}" if part else ""),
                    f"{label} tile {ti}/{tj}" + (f" part {part + 1}" if part else ""), len(chunk),
                    icon="mdi:map-marker-radius", state_class="measurement",
                    attributes={"fuel": fuel, "lat_from": ti * TILE_DEG, "lon_from": tj * TILE_DEG,
                                "size_deg": TILE_DEG, "stations": chunk},
                ))  # fmt: skip
    return Device("fuel", "Fuel prices", sensors)


POLLEN_TILE_DEG = 2.0  # pollen lattice points are about a degree apart; a tile holds a few


def pollen_point_key(lat: float, lon: float) -> str:
    """Stable sensor key for a lattice point, e.g. 52.25, -1.75 -> point_52p25_m1p75."""
    return "point_" + f"{round(lat, 3)}_{round(lon, 3)}".replace("-", "m").replace(".", "p")


def _pollen_point(p: dict[str, Any]) -> Sensor:
    """One forecast point as a sensor with latitude/longitude, so Home Assistant's map card shows it
    and opening it lists the allergy risk and each allergen for the coming days."""
    dates = p.get("dates") or []
    risk = p.get("allergy_risk") or []
    labels = p.get("allergy_risk_label") or []
    attrs: dict[str, Any] = {"latitude": p["lat"], "longitude": p["lon"], "friendly_name": f"Pollen {p['lat']}, {p['lon']}",
                             "allergy_risk": (labels[0] if labels else None), "dates": ", ".join(str(d) for d in dates),
                             "risk_by_day": ", ".join(str(r) for r in risk), "changed_at": p.get("changed_at")}  # fmt: skip
    for a in sorted(p.get("allergens") or [], key=lambda a: -(((a.get("levels") or [0])[0]) or 0)):
        lv = a.get("labels") or [("none", "low", "moderate", "high", "very high")[min(4, int(x or 0))] for x in a.get("levels") or []]
        attrs[str(a.get("name"))] = ", ".join(lv)  # worst first; one entry per day, today first
    return Sensor(pollen_point_key(p["lat"], p["lon"]), f"Pollen {p['lat']}, {p['lon']}", (risk[0] if risk else None),
                  icon="mdi:flower-pollen", state_class="measurement", attributes=attrs)  # fmt: skip


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
    sensors += [_pollen_point(p) for p in points]  # one per lattice point, so each can be opened on a map
    images = []
    if points:
        images.append(ha.Image("map", "Pollen map", render_svg(points).encode("utf-8")))
    return Device("pollen", "Pollen", sensors, images)


def events_device(data: dict[str, Any]) -> Device:
    """Events near each extra place Home Assistant asked for (not its home zone, which it fetches
    itself). One sensor per place, found by the dashboard by name, plus a summary of the cap."""
    lim = data.get("limits") or {}
    sensors = [Sensor(
        "locations", "Watched places", lim.get("count", 0), icon="mdi:map-marker-multiple", state_class="measurement",
        attributes={k: lim.get(k) for k in ("max", "pressure_at", "ttl_days", "refresh_hours", "provider_calls_per_day")},
    )]  # fmt: skip
    for loc in data.get("locations") or []:
        rows = [{"name": e.get("name"), "date": e.get("date"), "doors_open": e.get("doors_open"),
                 "venue": e.get("venue"), "town": e.get("town"), "distance_km": e.get("distance_km"),
                 "price": e.get("price"), "type": e.get("type"), "source": e.get("source"), "url": e.get("url")}
                for e in loc.get("events") or []]  # fmt: skip
        sensors.append(Sensor(
            f"place_{ha.slug(str(loc['id']))}", f"Events near {loc['name']}", loc.get("count", len(rows)),
            icon="mdi:calendar-star", state_class="measurement",
            attributes={"place": loc["name"], "latitude": loc.get("lat"), "longitude": loc.get("lon"),
                        "radius_km": loc.get("radius_km"), "days": loc.get("days"),
                        "expires_at": loc.get("expires_at"), "errors": loc.get("errors"), "events": rows},
        ))  # fmt: skip
    return Device("events", "Events", sensors)


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


def _local_hhmm(stamp: Any) -> str | None:
    """A timestamp from the API as the local clock time, e.g. 08:12."""
    from zoneinfo import ZoneInfo

    try:
        return datetime.fromisoformat(str(stamp)).astimezone(ZoneInfo(get_settings().local_tz)).strftime("%H:%M")
    except (TypeError, ValueError):
        return None


def _minutes_of_day(stamp: Any) -> int | None:
    hhmm = _local_hhmm(stamp)
    return None if hhmm is None else int(hhmm[:2]) * 60 + int(hhmm[3:])


def bus_device(
    tid: str, stops: list[dict[str, Any]], target: dict[str, Any] | None, live: dict[str, Any] | None,
    log: dict[str, Any] | None = None, ended: dict[str, Any] | None = None,
) -> Device:  # fmt: skip
    """One bus tracker. The day's answer, the chance of making the deadline and the success/failure
    record come from stored results (so they stay up to date outside the polling window); the buses
    being followed and the next arrivals come from the live arrivals feed. Names match what the poller
    used to publish itself, so existing entities carry on."""
    from app.integrations.bus.events import nearest_slot

    sensors: list[Sensor] = []
    nxt = (live or {}).get("next") or {}
    if target is not None:
        last = target.get("last_bus")
        hhmm = _local_hhmm(last.get("arrive_at")) if last else None
        sensors += [
            Sensor("last_bus", "Last bus to target", hhmm or "none", icon="mdi:bus-clock",
                   attributes={"deadline": target.get("deadline"), "earliest": target.get("earliest"),
                               "day": target.get("day"), "tracking_now": target.get("tracking_now"),
                               "last": last}),
            Sensor("hits_today", "Buses reaching target today", len(target.get("hits") or []),
                   icon="mdi:bus-multiple", state_class="measurement"),
            Sensor("pending_slots", "Scheduled buses not yet seen", len(target.get("pending") or []),
                   icon="mdi:bus-alert", state_class="measurement",
                   attributes={"scheduled": target.get("scheduled"), "pending": target.get("pending")}),
            Sensor("summary", "Target summary", (target.get("summary") or "")[:250], icon="mdi:text"),
        ]  # fmt: skip
        o = target.get("outlook") or {}
        pick = (o.get("slots") or [None])[0]
        board_rows = nxt.get(target.get("board")) or []
        if o.get("relevant") and board_rows and board_rows[0].get("expected"):  # the bus that is actually next
            m = _local_hhmm(board_rows[0]["expected"])
            slot = nearest_slot([x["slot"] for x in o["slots"]], m) if m else None
            pick = next((x for x in o["slots"] if x["slot"] == slot), pick)
        sensors.append(Sensor(
            "chance", f"Chance of reaching the target by {target.get('deadline')}",
            pick["chance"] if o.get("relevant") and pick else None, icon="mdi:percent", unit="%",
            state_class="measurement",
            attributes={"relevant": o.get("relevant"), "reason": o.get("reason"), "bus": pick and pick["slot"],
                        "based_on_days": pick and pick["days"], "based_on": pick and pick["basis"],
                        "deadline": target.get("deadline"), "days_recorded": o.get("days_recorded"),
                        "later_buses": (o.get("slots") or [])[1:5]},
        ))  # fmt: skip
    if log is not None:
        recent = [{"date": d["date"], "buses": [f"{b['slot']} {b['result']}" + (f" ({b['arrived']})" if b.get("arrived") else "")
                                                for b in d["buses"]]} for d in (log.get("days") or [])[:7]]
        sensors.append(Sensor(
            "record", "Target success rate", log.get("success_rate"), icon="mdi:chart-line", unit="%",
            state_class="measurement",
            attributes={"success": log.get("success"), "failure": log.get("failure"),
                        "window_days": log.get("window_days"), "recent_days": recent},
        ))  # fmt: skip
    if ended is not None:
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo(get_settings().local_tz)).date().isoformat()
        days = ended.get("days") or []
        todays = next((d for d in days if d["date"] == today), {"buses": [], "count": 0})

        def line(b: dict[str, Any]) -> str:
            where = b.get("stop") or "an unknown stop"
            back = f", next seen at {b['resumed_at_stop']} {b['resumed_at_time']}" if b.get("resumed_at_stop") else ""
            way = f" towards {b['towards']}" if b.get("towards") else ""
            return f"{b['time']} {b['kind']} at {where}{way} ({b.get('stops_before_end')} stops short){back}"

        sensors.append(Sensor(
            "ended_early", "Buses that ended early today", todays["count"], icon="mdi:bus-alert",
            state_class="measurement",
            attributes={"today": [line(b) for b in todays["buses"]],
                        "in_target_window_today": todays.get("in_target_window", 0),
                        "recent_days": [{"date": d["date"], "count": d["count"], "buses": [line(b) for b in d["buses"]]}
                                        for d in days[:7]]},
        ))  # fmt: skip
    active = (live or {}).get("active") or []
    sensors.append(Sensor("active_buses", "Buses being followed", len(active), icon="mdi:bus",
                          state_class="measurement", attributes={"buses": active}))  # fmt: skip
    for st in stops:
        rows = nxt.get(st.get("key")) or []
        sensors.append(Sensor(
            f"next_{st.get('key')}", f"Next bus: {st.get('label')}", rows[0].get("minutes") if rows else None,
            icon="mdi:bus-stop", unit="min", state_class="measurement", attributes={"next": rows},
        ))  # fmt: skip
    return Device(f"bus_{tid}", f"Bus tracker {tid}", sensors)


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
        for part, chunk in enumerate(_chunks(rows)):
            sensors.append(Sensor(
                f"tile_{ti}_{tj}".replace("-", "m") + (f"_p{part + 1}" if part else ""),
                f"Road events tile {ti}/{tj}" + (f" part {part + 1}" if part else ""), len(chunk),
                icon="mdi:map-marker-alert", state_class="measurement",
                attributes={"lat_from": ti * TILE_DEG, "lon_from": tj * TILE_DEG, "size_deg": TILE_DEG,
                            "events": chunk},
            ))  # fmt: skip
    return Device("roads", "Roads", sensors)


# Power sensors no longer published (0.21.1: carbon intensity dropped; the rota block is not looked up since 0.19.1)
POWER_RETIRED = ["carbon_intensity", "cleanest_start", "rota_block"]


def power_device(supply: dict[str, Any], cuts: dict[str, Any], works: dict[str, Any],
                 history: dict[str, Any] | None = None) -> Device:
    """Power at home (UK Power Networks): a cut there now, cuts nearby, street works nearby, and past cuts."""
    here = supply.get("cuts") or []
    first = here[0] if here else {}
    sensors = [
        Sensor("power_cut_home", "Power cut at home", "on" if supply.get("power_cut_now") else "off",
               icon="mdi:transmission-tower-off" if supply.get("power_cut_now") else "mdi:transmission-tower",
               attributes={"planned": first.get("planned"), "restore_by": first.get("restore_by"),
                           "customers": first.get("customers"), "status": first.get("status"),
                           "description": first.get("description")}),
        Sensor("power_cuts_near", "Power cuts near home", cuts.get("count", 0), icon="mdi:flash-alert",
               state_class="measurement", attributes={"cuts": (cuts.get("cuts") or [])[:20]}),
        Sensor("street_works_near", "Electricity street works near home", works.get("count", 0), icon="mdi:sign-caution",
               state_class="measurement", attributes={"open": works.get("open", 0), "proposed": works.get("proposed", 0),
                                                      "works": (works.get("works") or [])[:20]}),
    ]  # fmt: skip
    if history is not None:
        sensors.append(Sensor("cuts_at_home_year", "Power cuts at home, past year", history.get("unplanned", 0),
                              icon="mdi:history", state_class="measurement",
                              attributes={"planned": history.get("planned", 0), "longest_hours": history.get("longest_hours"),
                                          "total_hours": history.get("total_hours_unplanned"), "late": history.get("late", 0),
                                          "recording_since": history.get("recording_since")}))  # fmt: skip
    return Device("power", "Power", sensors)


EV_TILE_DEG = 1.0  # charging sites are few: one sensor per degree square (about 110 by 70 km) holds a handful


def ev_device(data: dict[str, Any]) -> Device:
    """Every charging site, nationwide, in map tiles (state: how many sites; attributes: each site's position,
    chargers working and free, by connector [total, working, free, kW], open now, price and how often one has been
    out of order), and a summary with the connector your electric vehicle takes. Home Assistant picks what is near
    whoever is looking."""
    sites = data.get("sites") or []
    sensors = [Sensor("summary", "EV chargers", data.get("count"), icon="mdi:ev-station", state_class="measurement",
                      attributes={"updated": data.get("updated"), "my_connector": data.get("my_connector"),
                                  "my_vehicle": data.get("my_vehicle"),
                                  "chargers": sum(s["total"] for s in sites), "working": sum(s["working"] for s in sites),
                                  "free": sum(s["free"] for s in sites)})]  # fmt: skip
    tiles: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for s in sites:
        tiles.setdefault((math.floor(s["lat"] / EV_TILE_DEG), math.floor(s["lon"] / EV_TILE_DEG)), []).append(
            {k: v for k, v in s.items() if k != "id"})
    for (ti, tj), rows in sorted(tiles.items()):
        for part, chunk in enumerate(_chunks(rows)):
            sensors.append(Sensor(
                f"tile_{ti}_{tj}".replace("-", "m") + (f"_p{part + 1}" if part else ""),
                f"EV chargers tile {ti}/{tj}" + (f" part {part + 1}" if part else ""), len(chunk),
                icon="mdi:map-marker-radius", state_class="measurement",
                attributes={"lat_from": ti * EV_TILE_DEG, "lon_from": tj * EV_TILE_DEG, "size_deg": EV_TILE_DEG,
                            "sites": chunk}))  # fmt: skip
    return Device("ev", "EV chargers", sensors)


def trips_device(trips: list[dict[str, Any]]) -> Device:
    """One status sensor and one planned-works sensor for each trip with a route."""

    def brief(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keys = ("source", "headline", "where", "starts", "ends", "level")
        return [{k: i.get(k) for k in keys} for i in items[:8]]

    sensors: list[Sensor] = []
    for t in trips:
        if "now" not in t:  # no route yet
            continue
        key, name = slug(str(t["trip"]["id"])), t["trip"].get("name") or t["trip"]["id"]
        planned = t["planned"]
        sensors.append(Sensor(
            f"{key}_conditions", f"{name}: conditions", t["now"]["status"], icon="mdi:road-variant",
            attributes={"summary": t.get("summary"), "problems": t["now"]["count"], "items": brief(t["now"]["items"])},
        ))  # fmt: skip
        sensors.append(Sensor(
            f"{key}_planned", f"{name}: planned works", planned["count"], icon="mdi:calendar-alert",
            state_class="measurement",
            attributes={"next_starts": planned["items"][0]["starts"] if planned["items"] else None,
                        "days": planned["days"], "items": brief(planned["items"])},
        ))  # fmt: skip
    if not sensors:
        raise ValueError("no trip has a route yet")
    return Device("trips", "Trips", sensors)


async def _get(c: httpx.AsyncClient, path: str, **params: Any) -> Any:
    r = await c.get(path, params=params or None)
    r.raise_for_status()
    return r.json()


async def collect(c: httpx.AsyncClient) -> list[Device]:
    """Ask the API for each domain. A domain that fails or isn't set up is skipped."""
    devices: list[Device] = []

    async def attempt(name: str, build: Any) -> None:
        try:
            built = await build()
            devices.extend(built if isinstance(built, list) else [built])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.info("home assistant: skipping %s (%s: %s)", name, type(exc).__name__, exc)

    async def pollen() -> Device:
        return pollen_device((await _get(c, "/v1/pollen/grid"))["points"])

    async def fuel() -> Device:
        return fuel_device({f: (await _get(c, "/v1/fuel/all", fuel=f, max_age_hours=get_settings().ha_fuel_max_age_h))["stations"] for f in FUELS})

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

    async def events() -> Device:
        return events_device(await _get(c, "/v1/ha/locations/events"))

    async def bus() -> list[Device]:
        out: list[Device] = []
        for t in await _get(c, "/v1/bus/trackers"):
            if not t.get("enabled"):
                continue
            tid = t["id"]
            try:
                cfg = (await _get(c, f"/v1/bus/trackers/{tid}"))["config"]
                target = await _get(c, f"/v1/bus/trackers/{tid}/target") if cfg.get("target") else None
                try:  # the arrivals feed is asked every time, so the next bus is there at any hour
                    live = await _get(c, f"/v1/bus/trackers/{tid}/live")
                except httpx.HTTPError:
                    live = None
                record = None
                if target is not None:
                    try:
                        record = await _get(c, f"/v1/bus/trackers/{tid}/log", days=30)
                    except httpx.HTTPError:
                        record = None
                try:
                    ended = await _get(c, f"/v1/bus/trackers/{tid}/terminations", days=7)
                except httpx.HTTPError:
                    ended = None
                out.append(bus_device(tid, cfg.get("stops") or [], target, live, record, ended))
            except httpx.HTTPError as exc:  # one dead tracker must not hide the rest
                log.info("home assistant: bus tracker %s skipped (%s)", tid, type(exc).__name__)
        return out

    async def energy() -> Device:
        now = await _get(c, "/v1/energy/now")
        if now.get("tariff") is None:
            raise ValueError("energy is not set up")
        if not now.get("available"):
            return energy_device(now, [])
        try:
            plan = await _get(c, "/v1/energy/schedule")
        except (httpx.HTTPError, ValueError):  # the rest of the energy sensors still go out
            plan = None
        return energy_device(now, (await _get(c, "/v1/energy/rates", hours=48))["slots"], plan)

    async def trips() -> Device:
        return trips_device((await _get(c, "/v1/conditions"))["trips"])

    async def power() -> Device:
        supply = await _get(c, "/v1/power/supply")
        if not supply.get("on_network"):
            raise ValueError("home is not on UK Power Networks' network (or not looked up yet)")
        return power_device(supply, await _get(c, "/v1/power/cuts"), await _get(c, "/v1/power/works"),
                            await _get(c, "/v1/power/history", days=365))  # fmt: skip

    async def ev() -> Device:
        data = await _get(c, "/v1/ev/all")
        if not data.get("count"):
            raise ValueError("no charging sites stored (GRIDSERVE not set up yet)")
        return ev_device(data)

    for name, build in (("ev", ev), ("trips", trips), ("energy", energy), ("power", power), ("pollen", pollen), ("fuel", fuel), ("trains", trains), ("roads", roads), ("events", events), ("bus", bus)):
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
    conf, _ = await load(ctx["engine"]) if "engine" in ctx else (from_env(s), "env")
    if not (conf.active and s.ha_publish):
        return 0
    async with httpx.AsyncClient(
        base_url=s.ha_api_url, timeout=30, headers={"Authorization": f"Bearer {s.api_token}"}
    ) as c:
        devices = await collect(c)
    if not devices:
        return 0
    fp = _fingerprint(devices)
    first = ctx.get("ha_fingerprint") != fp
    keys = {d.id: [x.key for x in d.sensors] for d in devices}
    gone: list[mqtt.Message] = []
    if first:  # sensors that existed at the last discovery but not now (an expired place): remove them
        for dev, old in (ctx.get("ha_keys") or {}).items():
            if dev in keys:
                gone += ha.clear(s.mqtt_topic_prefix, s.mqtt_discovery_prefix, dev, [k for k in old if k not in keys[dev]])
    if first and "energy" in keys:
        gone += ha.clear(s.mqtt_topic_prefix, s.mqtt_discovery_prefix, "energy", ENERGY_RETIRED)
    if first and "power" in keys:
        gone += ha.clear(s.mqtt_topic_prefix, s.mqtt_discovery_prefix, "power", POWER_RETIRED)
    expire = max(1, s.ha_interval_min) * 60 * 3
    sent: dict[str, str] = ctx.setdefault("ha_images", {})
    before = dict(sent)
    ok = await mqtt.publish(gone + messages(devices, with_discovery=first, expire_after=expire, sent_images=sent), conf)
    if ok:
        ctx["ha_fingerprint"] = fp
        ctx["ha_keys"] = keys
    else:
        ctx["ha_images"] = before  # not sent: try again next time
    sensors = sum(len(d.sensors) for d in devices)
    await record(ctx, ok, sensors)
    return sensors if ok else 0


HA_LAST_KEY = "wa:ha:last"


async def record(ctx: dict[str, Any], ok: bool, sensors: int) -> None:
    """What the last publish did, for the status page's setup checklist. Best effort."""
    redis = ctx.get("redis")
    if redis is None:
        return
    entry = {"at": datetime.now(UTC).isoformat(), "ok": ok, "sensors": sensors, "error": "" if ok else mqtt.last_error}
    try:
        await redis.set(HA_LAST_KEY, json.dumps(entry), ex=86400)
    except Exception:  # never stop publishing over a status note
        log.debug("could not record the publish result", exc_info=True)

"""Find my setup: read Home Assistant's own records over the Samba share and check everything World Assistant and
Home Assistant share.

Read (never written): .HA_VERSION and, in .storage, core.config (home, time zone), core.config_entries
(integrations; only safe fields such as the MQTT broker's host and port are kept, never passwords or keys),
core.entity_registry, core.device_registry, zone, person and lovelace_dashboards; plus World Assistant's own
packages and dashboards in the config folder.

From those it works out:
  - the names the shipped files are written with (app.ha_names): inverter, OCPP charger, OVMS car, Forecast.Solar;
  - whether World Assistant's MQTT sensors are in Home Assistant under the names the packages expect (Home Assistant
    renames one to ..._2 after a clash, which silently breaks a package);
  - whether Home Assistant's MQTT integration uses the broker World Assistant publishes to;
  - which World Assistant files are installed and whether each is current;
  - Home Assistant's version, home, time zone, zones and people (and whether home is World Assistant's home).

These are Home Assistant's internal files, not a published interface: everything is read defensively, and anything
not found is just reported as not found. The analysis (inventory) is pure, so it is tested without a share.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from app import ha_names as N
from app.auth import require_token

router = APIRouter(prefix="/v1/ha", tags=["home assistant"], dependencies=[Depends(require_token)])
INVENTORY_KEY = "ha_inventory"
STORAGE = ("core.config", "core.config_entries", "core.entity_registry", "core.device_registry", "zone", "person",
           "lovelace_dashboards")  # fmt: skip
# World Assistant's MQTT devices (app.ha_publish): an entity sensor.wa_<device>_<key> the packages name is published
WA_DEVICES = ("energy", "power", "pollen", "fuel", "events", "trains", "roads", "trips", "bus")
SOLAR_KEYS = {"solar_today": "energy_production_today", "solar_tomorrow": "energy_production_tomorrow",
              "solar_remaining": "energy_production_today_remaining", "solar_now": "power_production_now"}  # fmt: skip
WA_ENTITY = re.compile(r"\b(?:sensor|binary_sensor|image)\.wa_[a-z0-9_]+")
MIN_HA = (2024, 10)  # time triggers with an offset, the heading card, logbook card targets


def _data(raw: str | None) -> dict[str, Any]:
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return doc.get("data") or {} if isinstance(doc, dict) else {}


def _version(raw: str | None) -> tuple[int, int] | None:
    m = re.match(r"\s*(\d{4})\.(\d{1,2})", raw or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    x = math.radians(lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    return 6371.0 * math.hypot(x, math.radians(la2 - la1))


def _pick(found: list[str], current: str) -> str | None:
    """The best of the found names: the one already in use, else the first."""
    return current if current in found else (found[0] if found else None)


def names_from(entities: list[dict[str, Any]], entries: list[dict[str, Any]], current: dict[str, str]) -> dict[str, Any]:
    """Each name: what was found (the best one) and every candidate, from the entity registry and integrations."""
    ids = {e["entity_id"] for e in entities}
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for e in entities:
        by_platform.setdefault(e.get("platform") or "", []).append(e)
    out: dict[str, Any] = {}

    inverters = sorted({m.group(1) for i in ids if (m := re.match(r"number\.(.+)_time_slot_1_capacity$", i))
                        and f"sensor.{m.group(1)}_battery_soc" in ids})  # fmt: skip
    out["inverter"] = inverters

    chargers = sorted({m.group(1) for e in by_platform.get("ocpp", [])
                       if (m := re.match(r"switch\.(.+)_charge_control$", e["entity_id"]))})  # fmt: skip
    out["ocpp_charger"] = chargers

    vehicles = {str((x.get("data") or {}).get("vehicle_id", "")).lower() for x in entries if x.get("domain") == "ovms"}
    for e in by_platform.get("ovms", []):
        if m := re.match(r"(?:device_tracker\.(.+)_location|sensor\.(.+)_12v_battery_voltage)$", e["entity_id"]):
            vehicles.add(m.group(1) or m.group(2))
    out["ovms_vehicle"] = sorted(v for v in vehicles if v and N.TOKEN.match(v))

    for key, suffix in SOLAR_KEYS.items():
        out[key] = sorted(e["entity_id"] for e in by_platform.get("forecast_solar", [])
                          if str(e.get("unique_id", "")).endswith(suffix) and e["entity_id"].startswith("sensor."))  # fmt: skip

    return {k: {"found": _pick(v, current.get(k, "")), "candidates": v, "current": current.get(k)} for k, v in out.items()}


def inventory(raw: dict[str, str | None], current: dict[str, str], installed: dict[str, str | None],
              rendered: dict[str, str], wa_mqtt: dict[str, Any], wa_home: tuple[float, float] | None) -> dict[str, Any]:  # fmt: skip
    """Everything found and every problem, from Home Assistant's files (raw: name -> text, None when missing).
    installed / rendered: World Assistant's files as found in the config folder / as they would be written now."""
    cfg = _data(raw.get("core.config"))
    entries = [e for e in _data(raw.get("core.config_entries")).get("entries") or [] if isinstance(e, dict)]
    entities = [e for e in _data(raw.get("core.entity_registry")).get("entities") or []
                if isinstance(e, dict) and e.get("entity_id") and not e.get("disabled_by")]  # fmt: skip
    devices = [d for d in _data(raw.get("core.device_registry")).get("devices") or [] if isinstance(d, dict)]
    zones = [z for z in _data(raw.get("zone")).get("items") or [] if isinstance(z, dict)]
    persons = [p for p in _data(raw.get("person")).get("items") or [] if isinstance(p, dict)]
    dashboards = [d for d in _data(raw.get("lovelace_dashboards")).get("items") or [] if isinstance(d, dict)]
    version = (raw.get(".HA_VERSION") or "").strip() or None
    checks: list[dict[str, str]] = []

    def check(level: str, what: str, detail: str) -> None:
        checks.append({"level": level, "what": what, "detail": detail})

    if not entities:
        check("problem", "Registry", "Home Assistant's entity list (.storage/core.entity_registry) could not be read: "
                                     "is this the config share?")  # fmt: skip
    v = _version(version)
    if v and v < MIN_HA:
        check("problem", "Home Assistant version", f"{version}: the packages need {MIN_HA[0]}.{MIN_HA[1]} or later")
    elif v:
        check("ok", "Home Assistant version", version or "")

    # World Assistant's sensors, under the names the packages use
    mqtt_wa = [e for e in entities if e.get("platform") == "mqtt" and str(e.get("unique_id", "")).startswith("wa_")]
    renamed = sorted(f"{e['entity_id']} (expected {e['entity_id'].split('.')[0]}.{e['unique_id']})" for e in mqtt_wa
                     if e["entity_id"].split(".", 1)[1] != e["unique_id"])  # fmt: skip
    ids = {e["entity_id"] for e in entities}
    needed: set[str] = set()
    for path, body in installed.items():
        if body is not None and path.startswith("packages/"):
            needed |= {x for x in WA_ENTITY.findall(body) if x.split(".", 1)[1].split("_")[1] in WA_DEVICES}
    missing = sorted(needed - ids)
    if not mqtt_wa:
        check("problem", "World Assistant sensors", "none are in Home Assistant: is MQTT connected (step 1)?")
    else:
        check("ok", "World Assistant sensors", f"{len(mqtt_wa)} in Home Assistant")
    if renamed:
        check("problem", "Renamed sensors", "Home Assistant gave these other names, so packages naming them will not "
                                            "find them; rename them back in Settings > Entities: " + "; ".join(renamed[:20]))  # fmt: skip
    if missing:
        check("problem", "Sensors the packages need", "not in Home Assistant (World Assistant too old, that part not "
                                                      "set up, or not published yet): " + ", ".join(missing[:30]))  # fmt: skip

    # MQTT: the same broker?
    mq = next((e for e in entries if e.get("domain") == "mqtt"), None)
    ha_broker = None
    if mq:
        d = mq.get("data") or {}
        ha_broker = {"host": str(d.get("broker", "")), "port": int(d.get("port") or 1883)}
        wa_host, wa_port = str(wa_mqtt.get("host") or ""), int(wa_mqtt.get("port") or 0)
        if wa_host and (ha_broker["host"].lower(), ha_broker["port"]) != (wa_host.lower(), wa_port):
            check("check", "MQTT broker", f"Home Assistant uses {ha_broker['host']}:{ha_broker['port']}, World Assistant "
                                          f"publishes to {wa_host}:{wa_port}: fine if they are the same broker")  # fmt: skip
        elif wa_host:
            check("ok", "MQTT broker", f"both use {wa_host}:{wa_port}")
    else:
        check("problem", "MQTT", "Home Assistant has no MQTT integration: add it (step 1)")

    # home
    home = None
    if isinstance(cfg.get("latitude"), (int, float)) and isinstance(cfg.get("longitude"), (int, float)):
        home = {"name": cfg.get("location_name"), "lat": cfg["latitude"], "lon": cfg["longitude"],
                "time_zone": cfg.get("time_zone"), "country": cfg.get("country"), "currency": cfg.get("currency")}  # fmt: skip
        if wa_home:
            km = _km((home["lat"], home["lon"]), wa_home)
            if km > 1.0:
                check("check", "Home", f"Home Assistant's home is {km:.1f} km from World Assistant's home place: "
                                       "the events package and \"near home\" use Home Assistant's")  # fmt: skip
            else:
                check("ok", "Home", "the same as World Assistant's home place")

    # names
    names = names_from(entities, entries, current)
    for key, n in names.items():
        label = N.BY_KEY[key].label
        if n["found"] and n["found"] != current.get(key):
            check("change", label, f"found {n['found']} (the files say {current.get(key)}): save to use it")
        elif not n["candidates"]:
            check("info", label, "not found in Home Assistant: the files that need it will not work until it is set up")

    # World Assistant's files: installed and current?
    files = []
    for path, want in sorted(rendered.items()):
        have = installed.get(path)
        state = "not installed" if have is None else ("current" if have == want else "different")
        files.append({"path": path, "state": state})
    stale = [f["path"] for f in files if f["state"] == "different"]
    if stale:
        check("change", "Files to copy again", ", ".join(stale))
    ui_dash = [d.get("title") or d.get("url_path") for d in dashboards if d.get("mode") != "yaml"]

    integrations = sorted({(e.get("domain"), e.get("title")) for e in entries if not e.get("disabled_by")},
                          key=lambda x: (str(x[0]), str(x[1])))  # fmt: skip
    # fmt: off
    return {
        "read_at": datetime.now(UTC).isoformat(), "version": version, "home": home,
        "names": names, "checks": checks, "files": files,
        "wa_sensors": {"count": len(mqtt_wa), "renamed": renamed, "missing": missing},
        "mqtt": ha_broker,
        "integrations": [{"domain": d, "title": t} for d, t in integrations],
        "zones": [{"name": z.get("name"), "lat": z.get("latitude"), "lon": z.get("longitude"), "radius": z.get("radius")}
                  for z in zones],
        "persons": [p.get("name") for p in persons],
        "devices": len(devices), "entities": len(entities),
        "ui_dashboards": ui_dash,
    }
    # fmt: on


def read_share(share: Any, user: str, password: str, paths: list[str]) -> dict[str, str | None]:
    """Home Assistant's files and World Assistant's installed ones, over SMB (blocking: run in a thread)."""
    from app.ha_smb import SmbError, _close, _read, _session, _words

    cache: dict[str, Any] = {}
    out: dict[str, str | None] = {}
    try:
        _session(share, user, password, cache)
        for rel in [".HA_VERSION", *[f".storage/{n}" for n in STORAGE], *paths]:
            key = rel.removeprefix(".storage/")
            try:
                out[key] = _read(share.unc(rel), cache, share.port)
            except OSError as exc:
                raise SmbError(_words(exc, share)) from exc
        return out
    finally:
        _close(cache)


async def _wa_home(engine: Any) -> tuple[float, float] | None:
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT lat, lon FROM place ORDER BY (kind = 'home') DESC, (id = 'home') DESC LIMIT 1"))).first()  # fmt: skip
    except Exception:  # noqa: BLE001 - no places yet
        return None
    return (float(row[0]), float(row[1])) if row else None


@router.post("/discover")
async def discover(request: Request) -> dict[str, Any]:
    """Read Home Assistant's setup over the saved Samba share, check it, and keep the result (GET /v1/ha/inventory)."""
    from app.ha_setup import HA_DIR, files, render, saved_names
    from app.ha_smb import DASHBOARDS_DIR, PACKAGES_DIR, SmbError, _conf, _put
    from app.mqtt_config import load

    share, user, pw = await _conf(request)
    engine = request.app.state.engine
    current = await saved_names(engine)
    ours = {f["path"]: f for f in files()}
    where = {p: (f"{PACKAGES_DIR}/{f['name']}" if f["kind"] == "package" else f"{DASHBOARDS_DIR}/{f['name']}")
             for p, f in ours.items()}  # fmt: skip
    try:
        raw = await asyncio.to_thread(read_share, share, user, pw, list(where.values()))
    except SmbError as exc:
        raise HTTPException(502, str(exc)) from exc
    conf, _ = await load(engine)
    installed = {p: raw.get(w) for p, w in where.items()}
    result = inventory(raw, current, installed,
                       {p: render((HA_DIR / p).read_text(), current) for p in ours},
                       {"host": conf.host, "port": conf.port}, await _wa_home(engine))  # fmt: skip
    await _put(engine, INVENTORY_KEY, result)
    return result


@router.get("/inventory")
async def last_inventory(request: Request) -> dict[str, Any]:
    """What the last "Find my setup" found (empty until it has run)."""
    from app.ha_smb import _get

    return await _get(request.app.state.engine, INVENTORY_KEY) or {}

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

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import ha_names as N
from app.auth import require_token

router = APIRouter(prefix="/v1/ha", tags=["home assistant"], dependencies=[Depends(require_token)])
INVENTORY_KEY = "ha_inventory"
API_KEY, TOKEN_SECRET = "ha_api", "ha_token"  # Home Assistant's address (database) and long-lived token (keys folder)
ZONES_FILE = "packages/wa_zones.yaml"
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


def editor_dashboards(items: list[dict[str, Any]], raw: dict[str, str | None]) -> list[dict[str, Any]]:
    """Dashboards made in Home Assistant's editor, with their config (from .storage/lovelace.<id>)."""
    out = []
    for d in items:
        if d.get("mode") == "yaml" or not d.get("id"):
            continue
        cfg = _data(raw.get(f"lovelace.{d['id']}")).get("config")
        out.append({"title": d.get("title") or d.get("url_path"), "config": cfg if isinstance(cfg, dict) else None})
    return out


def pasted_state(rendered: str, editor: list[dict[str, Any]]) -> str | None:
    """A shipped dashboard pasted into the editor: the same as it would be written now, or not (comments aside)."""
    import yaml

    try:
        want = yaml.safe_load(rendered)
    except yaml.YAMLError:
        return None
    if not isinstance(want, dict):
        return None
    title = want.get("title")
    for d in editor:
        if d["config"] is not None and title and title in (d["title"], d["config"].get("title")):
            return "pasted, current" if d["config"] == want else "pasted, different"
    return None


def match_zones(zones: list[dict[str, Any]], places: list[dict[str, Any]],
                home: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:  # fmt: skip
    """Each zone with the World Assistant place at it (or None), and the places with no zone (home is Home
    Assistant's home, which is not a zone)."""
    rows, used = [], set()
    for z in zones:
        if not isinstance(z.get("latitude"), (int, float)) or not isinstance(z.get("longitude"), (int, float)):
            continue
        reach = max(float(z.get("radius") or 100), 150.0) / 1000
        hit = next((p for p in places if p.get("lat") is not None
                    and _km((z["latitude"], z["longitude"]), (p["lat"], p["lon"])) <= reach), None)  # fmt: skip
        if hit:
            used.add(hit["id"])
        rows.append({"name": z.get("name"), "lat": z["latitude"], "lon": z["longitude"], "radius": z.get("radius"),
                     "place": hit["id"] if hit else None})  # fmt: skip
    rest = []
    for p in places:
        if p["id"] in used or p.get("lat") is None:
            continue
        if home and _km((home["lat"], home["lon"]), (p["lat"], p["lon"])) <= 0.15:
            continue
        rest.append({"id": p["id"], "name": p["name"], "kind": p.get("kind"), "lat": p["lat"], "lon": p["lon"]})
    return rows, rest


def key_entities(names: dict[str, str]) -> dict[str, str]:
    """One entity per name that should report a value when that name is right."""
    return {"inverter": f"sensor.{names['inverter']}_battery_soc",
            "ocpp_charger": f"sensor.{names['ocpp_charger']}_status_connector",
            "ovms_vehicle": f"sensor.{names['ovms_vehicle']}_12v_battery_voltage",
            **{k: names[k] for k in SOLAR_KEYS}}  # fmt: skip


def live_checks(check: Any, live: dict[str, Any] | None, names: dict[str, str], registry: set[str]) -> None:
    """With Home Assistant's live states: does each named entity report, and are World Assistant's sensors fresh?"""
    if not live:
        return
    if live.get("error"):
        check("problem", "Home Assistant API", live["error"])
        return
    states = {s.get("entity_id"): s for s in live.get("states") or [] if isinstance(s, dict)}
    check("ok", "Home Assistant API", f"{len(states)} entities read live")
    dead, fine = [], []
    for entity in key_entities(names).values():
        if entity not in states and entity not in registry:
            continue  # not set up here: the registry check already says so
        st = (states.get(entity) or {}).get("state")
        (fine if st not in (None, "unknown", "unavailable") else dead).append(f"{entity} ({st or 'missing'})")
    if dead:
        check("problem", "Entities not reporting", ", ".join(dead))
    if fine:
        check("ok", "Entities reporting", ", ".join(fine))
    wa = [s for e, s in states.items() if str(e).startswith(("sensor.wa_", "binary_sensor.wa_"))]
    stale = sorted(str(s.get("entity_id")) for s in wa if s.get("state") == "unavailable")
    if stale:
        check("problem", "World Assistant sensors unavailable", f"{len(stale)} of {len(wa)} (not published lately, or "
                                                                f"the part is off): " + ", ".join(stale[:15]))  # fmt: skip
    elif wa:
        newest = max((str(s.get("last_updated") or "") for s in wa), default="")
        check("ok", "World Assistant sensors live", f"{len(wa)}, last updated {newest[:16].replace('T', ' ')}")


def inventory(raw: dict[str, str | None], current: dict[str, str], installed: dict[str, str | None],
              rendered: dict[str, str], wa_mqtt: dict[str, Any], wa_home: tuple[float, float] | None,
              places: list[dict[str, Any]] | None = None, live: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """Everything found and every problem, from Home Assistant's files (raw: name -> text, None when missing).
    installed / rendered: World Assistant's files as found in the config folder / as they would be written now.
    places: World Assistant's places (id, name, kind, lat, lon). live: Home Assistant's states through its API
    ({"states": [...]} or {"error": "..."}), when a token is set."""
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
    editor = editor_dashboards(dashboards, raw)
    files = []
    for path, want in sorted(rendered.items()):
        have = installed.get(path)
        state = "not installed" if have is None else ("current" if have == want else "different")
        pasted = None
        if path.startswith("dashboards/"):
            pasted = pasted_state(want, editor)
            if state == "not installed" and pasted:
                state = pasted
        files.append({"path": path, "state": state, "pasted": pasted})
    stale = [f["path"] for f in files if f["state"] in ("different", "pasted, different")]
    if stale:
        check("change", "Files to copy again", ", ".join(stale))
    ui_dash = [d.get("title") or d.get("url_path") for d in dashboards if d.get("mode") != "yaml"]

    zone_rows, places_only = match_zones(zones, places or [], home)
    if zone_rows and any(z["place"] is None for z in zone_rows):
        check("info", "Zones", "Home Assistant zones that are not World Assistant places: "
              + ", ".join(z["name"] for z in zone_rows if z["place"] is None) + " (import them below)")  # fmt: skip
    if places_only:
        check("info", "Places", "World Assistant places that are not Home Assistant zones: "
              + ", ".join(p["name"] for p in places_only) + " (copy them as zones below)")  # fmt: skip
    live_checks(check, live, current, ids)

    integrations = sorted({(e.get("domain"), e.get("title")) for e in entries if not e.get("disabled_by")},
                          key=lambda x: (str(x[0]), str(x[1])))  # fmt: skip
    # fmt: off
    return {
        "read_at": datetime.now(UTC).isoformat(), "version": version, "home": home,
        "names": names, "checks": checks, "files": files,
        "wa_sensors": {"count": len(mqtt_wa), "renamed": renamed, "missing": missing},
        "mqtt": ha_broker,
        "integrations": [{"domain": d, "title": t} for d, t in integrations],
        "zones": zone_rows, "places_not_zones": places_only, "live": bool(live and live.get("states")),
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
        def get(rel: str) -> None:
            try:
                out[rel.removeprefix(".storage/")] = _read(share.unc(rel), cache, share.port)
            except OSError as exc:
                raise SmbError(_words(exc, share)) from exc

        for rel in [".HA_VERSION", *[f".storage/{n}" for n in STORAGE], *paths]:
            get(rel)
        for d in (_data(out.get("lovelace_dashboards")).get("items") or [])[:40]:  # the editor's dashboards
            if isinstance(d, dict) and d.get("mode") != "yaml" and re.match(r"^[a-z0-9_]+$", str(d.get("id", ""))):
                get(f".storage/lovelace.{d['id']}")
        return out
    finally:
        _close(cache)


async def _places(engine: Any) -> list[dict[str, Any]]:
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(text("SELECT id, name, kind, lat, lon FROM place ORDER BY name"))
            return [dict(r._mapping) for r in rows]
    except Exception:  # noqa: BLE001 - no places table yet
        return []


async def _api(engine: Any) -> tuple[str, str] | None:
    """Home Assistant's address and token, when both are set."""
    from app import secretstore
    from app.config import SECRETS_DIR
    from app.ha_smb import _get

    conf = await _get(engine, API_KEY) or {}
    token = secretstore.read_secret(TOKEN_SECRET, SECRETS_DIR)
    return (conf["url"], token) if conf.get("url") and token else None


async def live_states(url: str, token: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Home Assistant's current states through its API, or the reason they could not be read."""
    try:
        async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=15.0, transport=transport,
                                     headers={"Authorization": f"Bearer {token}"}) as c:  # fmt: skip
            r = await c.get("/api/states")
    except httpx.HTTPError as exc:
        return {"error": f"could not reach {url}: {type(exc).__name__}"}
    if r.status_code == 401:
        return {"error": "Home Assistant refused the token (401): make a new long-lived token"}
    if r.status_code != 200:
        return {"error": f"Home Assistant answered HTTP {r.status_code}"}
    try:
        return {"states": r.json()}
    except ValueError:
        return {"error": "Home Assistant's answer was not JSON: is the address its own (port 8123)?"}


def zones_package(places: list[dict[str, Any]]) -> str:
    """World Assistant places as Home Assistant zones (a package: Home Assistant loads zone: from YAML)."""
    icon = {"home": "mdi:home", "work": "mdi:briefcase"}
    lines = ["# World Assistant places as Home Assistant zones. Made by World Assistant (Find my setup on /ha);",
             "# copying again replaces this file. Zones you made in Home Assistant itself are not touched.", "zone:"]
    for p in places:
        lines += [f"  - name: {json.dumps(p['name'])}", f"    latitude: {round(float(p['lat']), 6)}",
                  f"    longitude: {round(float(p['lon']), 6)}", "    radius: 150",
                  f"    icon: {icon.get(str(p.get('kind')), 'mdi:map-marker')}"]  # fmt: skip
    return "\n".join(lines) + "\n"


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
    api = await _api(engine)
    live = await live_states(*api) if api else None
    result = inventory(raw, current, installed,
                       {p: render((HA_DIR / p).read_text(), current) for p in ours},
                       {"host": conf.host, "port": conf.port}, await _wa_home(engine), await _places(engine), live)  # fmt: skip
    await _put(engine, INVENTORY_KEY, result)
    return result


@router.get("/inventory")
async def last_inventory(request: Request) -> dict[str, Any]:
    """What the last "Find my setup" found (empty until it has run)."""
    from app.ha_smb import _get

    return await _get(request.app.state.engine, INVENTORY_KEY) or {}


# --- zones and places ----------------------------------------------------------------------------------------------


class ZonesIn(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=100)


@router.post("/zones/import")
async def import_zones(body: ZonesIn, request: Request) -> dict[str, Any]:
    """Make World Assistant places of these Home Assistant zones (from the last "Find my setup")."""
    from app.ha_smb import _get

    inv = await _get(request.app.state.engine, INVENTORY_KEY) or {}
    zones = {z["name"]: z for z in inv.get("zones") or [] if z.get("place") is None}
    have = {p["id"] for p in await _places(request.app.state.engine)}
    made, skipped = [], []
    for name in body.names:
        z = zones.get(name)
        pid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
        if z is None or not pid:
            skipped.append(f"{name}: not a zone without a place (run Find my setup again)")
            continue
        if not (49.5 <= float(z["lat"]) <= 61.0 and -8.7 <= float(z["lon"]) <= 2.0):
            skipped.append(f"{name}: outside the UK")
            continue
        if pid in have:
            pid = f"{pid}-ha"
        kind = "work" if name.strip().lower() == "work" else "other"
        async with request.app.state.engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO place (id, name, kind, lat, lon) VALUES (:id, :name, :kind, :lat, :lon) "
                "ON CONFLICT (id) DO NOTHING"), {"id": pid, "name": name, "kind": kind, "lat": z["lat"], "lon": z["lon"]})  # fmt: skip
        made.append(pid)
        have.add(pid)
    return {"made": made, "skipped": skipped}


@router.get("/zones/package", response_class=PlainTextResponse)
async def zones_file(request: Request) -> PlainTextResponse:
    """World Assistant's places that are not Home Assistant zones, as a package of zones (from the last "Find my
    setup"; all places if it has not run)."""
    from app.ha_smb import _get

    inv = await _get(request.app.state.engine, INVENTORY_KEY) or {}
    places = inv["places_not_zones"] if "places_not_zones" in inv else await _places(request.app.state.engine)
    return PlainTextResponse(zones_package([p for p in places if p.get("lat") is not None]),
                             media_type="text/yaml; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="wa_zones.yaml"'})  # fmt: skip


@router.post("/zones/copy")
async def copy_zones(request: Request) -> dict[str, Any]:
    """Write that package into Home Assistant's packages folder over the saved Samba share."""
    from app.ha_smb import _conf, copy_files

    share, user, pw = await _conf(request)
    body = (await zones_file(request)).body.decode()
    return await asyncio.to_thread(copy_files, share, user, pw, {ZONES_FILE: body}, None)


# --- Home Assistant's API (optional, for live checks) ----------------------------------------------------------------


class ApiIn(BaseModel):
    url: str = Field(..., min_length=8, max_length=200)
    token: str | None = Field(None, max_length=600)  # empty: keep the saved one


@router.get("/api")
async def get_api(request: Request) -> dict[str, Any]:
    from app import secretstore
    from app.config import SECRETS_DIR
    from app.ha_smb import _get

    conf = await _get(request.app.state.engine, API_KEY) or {}
    return {"url": conf.get("url", ""), "token_set": bool(secretstore.read_secret(TOKEN_SECRET, SECRETS_DIR))}


@router.put("/api")
async def put_api(body: ApiIn, request: Request) -> dict[str, Any]:
    """Home Assistant's address and a long-lived token (Profile > Security), for live checks in Find my setup.
    The token is kept in the keys folder, readable only by World Assistant, and never shown again."""
    from app.ha_smb import _put, _save_secret

    url = body.url.strip().rstrip("/")
    if not re.match(r"^https?://", url):
        url = "http://" + url
    if not re.match(r"^https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~/-]*)?$", url):
        raise HTTPException(422, "Home Assistant's address looks like http://10.0.2.107:8123")
    if body.token:
        _save_secret(TOKEN_SECRET, body.token.strip())
    await _put(request.app.state.engine, API_KEY, {"url": url})
    api = await _api(request.app.state.engine)
    test = await live_states(*api) if api else {"error": "no token saved yet"}
    return {**(await get_api(request)), "ok": "states" in test, "error": test.get("error"),
            "entities": len(test.get("states") or [])}


@router.delete("/api")
async def forget_api(request: Request) -> dict[str, Any]:
    from app import secretstore

    path = secretstore.keys_dir() / f"{TOKEN_SECRET}.txt"
    if path.exists():
        path.unlink()
    secretstore.clear_cache()
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text("DELETE FROM app_setting WHERE key = :k"), {"k": API_KEY})
    return await get_api(request)

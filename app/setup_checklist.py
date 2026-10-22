"""What is set up and working, for the status page: each data source, Home Assistant, routing and home.

Reads only what this application knows: which keys are present (never their values), the last run of each
job, and what the worker last managed to publish to Home Assistant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.auth import require_token
from app.config import Settings, get_settings
from app.ha_publish import HA_LAST_KEY
from app.localnet import is_local

router = APIRouter(prefix="/v1/setup", tags=["status"], dependencies=[Depends(require_token)])

# States, best first: ready (working), limited (works, better with a key), waiting (set up, no run yet),
# off (turned off here), missing (needs a key or a step), failing (set up, but the last run failed).
READY, LIMITED, WAITING, OFF, MISSING, FAILING = "ready", "limited", "waiting", "off", "missing", "failing"


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    unlocks: str
    keys: tuple[str, ...] = ()  # secret names; empty = needs no key
    any_key: bool = False  # one of the keys is enough (events)
    key_optional: bool = False  # works without, better with
    jobs: tuple[str, ...] = ()  # job_run names; empty = asked live, no stored runs
    switch: str = ""  # Settings attribute that turns the poll on
    link: str = ""
    note: str = ""
    extra_ready: tuple[str, ...] = field(default=())  # Settings attributes that count as "set up" too


SOURCES: tuple[Source, ...] = (
    Source("nh", "National Highways", "Closures, speed limits and signs on motorways and major A roads",
           keys=("nh_subscription_key",), jobs=("nh_poll_live",), switch="nh_poll_enabled",
           link="https://developer.data.nationalhighways.co.uk/"),
    Source("fuel", "Fuel Finder (GOV.UK)", "Prices at every UK fuel station, cheapest near you or on the way",
           keys=("fuel_finder_client_id", "fuel_finder_client_secret"), jobs=("fuel_prices", "fuel_stations"),
           switch="fuel_poll_enabled",
           link="https://www.gov.uk/guidance/access-the-latest-fuel-prices-and-forecourt-data-via-api-or-email",
           note="The API only answers UK addresses."),
    Source("tfl", "Transport for London", "London bus times and the bus tracker, train boards, London road works",
           keys=("tfl_app_key",), key_optional=True, jobs=("tflroads", "bus_poll"),
           link="https://api-portal.tfl.gov.uk/", note="Works without a key at a low rate."),
    Source("nr", "National Rail", "Live departures from any station in Great Britain",
           keys=("nr_access_token",), link="https://raildata.org.uk/"),
    Source("bods", "Bus Open Data Service", "Live bus positions anywhere in Britain; GPS for the bus tracker",
           keys=("bods_api_key",), link="https://data.bus-data.dft.gov.uk/"),
    Source("events", "Skiddle and Ticketmaster", "What is on near a place: gigs, comedy, theatre, sport",
           keys=("skiddle_api_key", "ticketmaster_api_key"), any_key=True,
           link="https://developer.ticketmaster.com/", note="Either key is enough; Skiddle's is at skiddle.com/api."),
    Source("pollen", "Polleninformation", "Pollen by allergen, today and three days ahead",
           keys=("pollen_api_key",), jobs=("pollen_grid_sync", "pollen_sync"),
           link="https://www.polleninformation.at/en/data-interface", note="Free for personal use; 40 requests a day."),
    Source("octopus", "Octopus Energy", "Your electricity rates, cheap windows and Saving Sessions",
           keys=("octopus_api_key",), jobs=("energy",), switch="energy_poll_enabled",
           link="https://octopus.energy/dashboard/new/accounts/personal-details/api-access",
           note="Rates also work with just a tariff code (WA_OCTOPUS_TARIFF).",
           extra_ready=("octopus_tariff",)),
    Source("floods", "Environment Agency", "Flood alerts and warnings in England, on the map and on your routes",
           jobs=("flood",), switch="flood_poll_enabled"),
    Source("tflroads", "TfL road disruptions", "London works, closures and incidents near your routes",
           jobs=("tflroads",), switch="tflroads_poll_enabled"),
    Source("naptan", "Bus stops and stations (NaPTAN)", "Every stop and station in Great Britain",
           jobs=("naptan",), switch="naptan_poll_enabled", note="About 100 MB, loaded on first start."),
    Source("codepoint", "Postcodes (OS Code-Point Open)", "Places by postcode without an outside service",
           jobs=("codepoint",), switch="codepoint_poll_enabled", note="About 25 MB, loaded on first start."),
)  # fmt: skip


def _has(s: Settings, name: str) -> bool:
    try:
        return bool(str(getattr(s, name, "") or "").strip())
    except Exception:  # noqa: BLE001 - a vault that cannot be reached: report it as not there
        return False


def _key_state(src: Source, s: Settings) -> tuple[bool, list[str]]:
    """(usable, missing key names)."""
    present = {k: _has(s, k) for k in src.keys}
    missing = [k for k, ok in present.items() if not ok]
    if not src.keys:
        return True, []
    if src.key_optional or any(_has(s, x) for x in src.extra_ready):
        return True, missing
    ok = any(present.values()) if src.any_key else not missing
    return ok, missing


def judge(src: Source, s: Settings, runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One checklist row. `runs`: the latest job_run per job name."""
    usable, missing = _key_state(src, s)
    out: dict[str, Any] = {"id": src.id, "name": src.name, "unlocks": src.unlocks, "needs_key": bool(src.keys),
                           "link": src.link, "note": src.note, "missing_keys": missing}  # fmt: skip
    mine = [runs[j] for j in src.jobs if j in runs]
    last_ok = max((r["finished_at"] for r in mine if r["status"] == "ok" and r["finished_at"]), default=None)
    out["last_ok"] = last_ok.isoformat() if isinstance(last_ok, datetime) else last_ok
    if src.switch and not getattr(s, src.switch, True):
        out["state"] = OFF
        return out
    if not usable:
        out["state"] = MISSING
        return out
    latest = max(mine, key=lambda r: r["started_at"], default=None)
    if latest and latest["status"] == "error":
        out["state"], out["error"] = FAILING, (latest.get("error") or "")[:300]
    elif src.jobs and not mine:
        out["state"] = WAITING
    elif src.key_optional and missing:
        out["state"] = LIMITED
    else:
        out["state"] = READY
    return out


def home_assistant(s: Settings, last: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": "ha", "name": "Home Assistant (MQTT)", "link": "/ha",
                           "unlocks": "Sensors and ready-made dashboards in Home Assistant",
                           "broker": f"{s.mqtt_host}:{s.mqtt_port}" if s.mqtt_host else ""}  # fmt: skip
    if not s.mqtt_active or not s.ha_publish:
        out["state"] = OFF
        out["note"] = "No broker set. Set WA_MQTT_HOST (or run scripts/setup.sh) to publish sensors."
    elif not last:
        out["state"] = WAITING
    elif last.get("ok"):
        out["state"], out["last_ok"], out["note"] = READY, last.get("at"), f"{last.get('sensors', 0)} sensors"
    else:
        out["state"], out["error"] = FAILING, last.get("error") or "could not reach the broker"
    return out


async def routing(s: Settings) -> dict[str, Any]:
    out: dict[str, Any] = {"id": "routing", "name": "Routing (Valhalla)",
                           "unlocks": "Real road routes for trips, so road events, fuel and conditions follow them",
                           "note": "Optional container: docker-compose.routing.yml (downloads about 2 GB)."}  # fmt: skip
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get(s.valhalla_url.rstrip("/") + "/status")
        out["state"] = READY if r.status_code == 200 else WAITING
    except httpx.HTTPError:
        out["state"] = OFF
    return out


async def checklist(request: Request) -> dict[str, Any]:
    s = get_settings()
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT DISTINCT ON (job) job, started_at, finished_at, status, error FROM job_run "
            "ORDER BY job, started_at DESC"))).mappings().all()  # fmt: skip
        home = (await conn.execute(text("SELECT count(*) FROM place WHERE kind = 'home' OR id = 'home'"))).scalar_one()
    runs = {r["job"]: dict(r) for r in rows}
    try:
        raw = await request.app.state.redis.get(HA_LAST_KEY)
        last = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - the page must still show the rest
        last = None
    keyed = [judge(x, s, runs) for x in SOURCES if x.keys]
    free = [judge(x, s, runs) for x in SOURCES if not x.keys]
    you = [
        {"id": "home", "name": "Your home", "unlocks": "The default place for fuel, events, pollen and the map",
         "state": READY if home else MISSING, "link": "/places",
         "note": "" if home else "Add a place called home on the Places page."},
        home_assistant(s, last),
        await routing(s),
    ]  # fmt: skip
    return {"local": is_local(request, request.method), "you": you, "keys": keyed, "free": free}


@router.get("")
async def setup(request: Request) -> dict[str, Any]:
    """The setup checklist: what each source unlocks, whether it is set up and working, and where to get a key."""
    return await checklist(request)

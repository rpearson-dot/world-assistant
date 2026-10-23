"""What is set up and working, for the status page: each data source, Home Assistant, routing and home.

Reads only what this application knows: which keys are present (never their values), the last run of each
job, and what the worker last managed to publish to Home Assistant.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.auth import require_token
from app.config import Settings, get_settings
from app.ha_publish import HA_LAST_KEY
from app.localnet import is_local
from app.modules import registry
from app.modules.model import Module
from app.mqtt_config import MqttConf, from_env, load

router = APIRouter(prefix="/v1/setup", tags=["status"], dependencies=[Depends(require_token)])

# States, best first: ready (working), limited (works, better with a key), waiting (set up, no run yet),
# off (turned off here), missing (needs a key or a step), failing (set up, but the last run failed).
READY, LIMITED, WAITING, OFF, MISSING, FAILING = "ready", "limited", "waiting", "off", "missing", "failing"


# A data source on this list is a module (app/modules). The row for each comes from its declaration.
Source = Module


def sources() -> tuple[Module, ...]:
    return tuple(m for m in registry.modules() if m.listed)


SOURCES = sources()  # the built-ins; outside modules appear in sources()


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
                           "link": src.link, "note": src.note, "missing_keys": missing,
                           "keys": list(src.keys), "any_key": src.any_key, "key_optional": src.key_optional}  # fmt: skip
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


def home_assistant(s: Settings, last: dict[str, Any] | None, conf: MqttConf | None = None) -> dict[str, Any]:
    conf = conf or from_env(s)
    out: dict[str, Any] = {"id": "ha", "name": "Home Assistant (MQTT)", "link": "/settings#mqtt",
                           "unlocks": "Sensors and ready-made dashboards in Home Assistant",
                           "broker": f"{conf.host}:{conf.port}" if conf.host else "", "tls": conf.tls}  # fmt: skip
    if not conf.active or not s.ha_publish:
        out["state"] = OFF
        out["note"] = "No broker set: add it under Settings > MQTT."
    elif not last:
        out["state"] = WAITING
        out["note"] = f"Publishing to {conf.host}:{conf.port}" + (" (encrypted)." if conf.tls else ".")
    elif last.get("ok"):
        out["state"], out["last_ok"] = READY, last.get("at")
        out["note"] = f"{last.get('sensors', 0)} sensors on {conf.host}:{conf.port}" + (", encrypted." if conf.tls else ".")
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


async def graph(s: Settings) -> dict[str, Any]:
    out: dict[str, Any] = {"id": "graph", "name": "Knowledge graph (Neo4j)", "link": "/graph",
                           "unlocks": "The Graph page: how your places, trips, roads and stations connect"}  # fmt: skip
    if not s.graph_enabled:
        out["state"], out["note"] = OFF, "Optional container: docker-compose.graph.yml and WA_GRAPH_ENABLED=true."
        return out
    from app.graph import store

    try:
        rows = await store.run(store.COUNTS)
    except store.GraphUnavailable as exc:
        out["state"], out["error"] = FAILING, str(exc)[:300]
        return out
    total = sum(int(r["total"]) for r in rows)
    out["state"] = READY if total else WAITING
    out["note"] = f"{total} nodes" if total else "Built twice an hour; or Rebuild on the Graph page."
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
    keyed = [judge(x, s, runs) for x in sources() if x.keys]
    free = [judge(x, s, runs) for x in sources() if not x.keys]
    you = [
        {"id": "home", "name": "Your home", "unlocks": "The default place for fuel, events, pollen and the map",
         "state": READY if home else MISSING, "link": "/places",
         "note": "" if home else "Add a place called home on the Places page."},
        home_assistant(s, last, (await load(request.app.state.engine))[0]),
        await routing(s),
        await graph(s),
    ]  # fmt: skip
    return {"local": is_local(request, request.method), "you": you, "keys": keyed, "free": free}


@router.get("")
async def setup(request: Request) -> dict[str, Any]:
    """The setup checklist: what each source unlocks, whether it is set up and working, and where to get a key."""
    return await checklist(request)

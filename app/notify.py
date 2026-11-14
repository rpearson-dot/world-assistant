"""Notifications to your phone, through an ntfy server (docker-compose.tailscale.yml runs one, reached over the tailnet).

    GET|PUT /v1/notify/settings   on or off, which kinds, quiet hours; the server and topic to subscribe to
    POST /v1/notify/test          send a test now
    GET /v1/notify/log            the last notifications sent

Kinds: bikes (low sensor and AXS batteries, parts newly due, a new sensor, after each Garmin sync), firmware (a
device's software changed), environment (flood warnings, high air pollution, very high pollen at your places) and
system (a Garmin sync failing, a Garmin key running out, a safety recall). Each is sent once (by its key, kept in
notify_log). In quiet hours a notification is held by ntfy until they end, unless it is urgent (a severe flood
warning). The topic is a long random name made the first time; anyone on your tailnet who knows it could read it.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth import require_token
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/notify", tags=["notifications"], dependencies=[Depends(require_token)])
KEY = "notify"
KINDS = {"bikes": "Bike batteries and parts due", "firmware": "Firmware changes",
         "environment": "Environment warnings", "home": "Car, charger and home (charger faults and restarts, car warnings)",
         "health": "World Assistant itself (a stopped worker or job, a full disk, backups)",
         "system": "System (sync failures, keys running out, recalls)",
         "modules": "Outside modules that need your attention (an update held, falling behind, a limit reached)",
         "network": "A new device on your network"}  # fmt: skip
DEFAULTS: dict[str, Any] = {"enabled": True, "kinds": {"bikes": True, "firmware": False, "environment": True, "home": True, "health": True, "system": False, "modules": True, "network": True},
                            "quiet_from": "22:00", "quiet_to": "07:00", "topic": None}  # fmt: skip


async def settings(engine: AsyncEngine) -> dict[str, Any]:
    """The saved settings over the defaults; a topic is made (and saved) the first time."""
    async with engine.connect() as c:
        row = (await c.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).first()
    v = row[0] if row else {}
    if isinstance(v, str):
        v = json.loads(v)
    out = {**DEFAULTS, **(v or {}), "kinds": {**DEFAULTS["kinds"], **((v or {}).get("kinds") or {})}}
    if not out.get("topic"):
        out["topic"] = "wa-" + secrets.token_urlsafe(18).replace("_", "x").replace("-", "y")
        await _save(engine, out)
    return out


async def _save(engine: AsyncEngine, v: dict[str, Any]) -> None:
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                        {"k": KEY, "v": json.dumps(v)})  # fmt: skip


def _hm(s: str) -> time:
    h, m = (int(x) for x in str(s).split(":"))
    return time(h, m)


def quiet_until(st: dict[str, Any], now: datetime) -> datetime | None:
    """When the quiet hours now in force end (None outside them, or with none set)."""
    try:
        a, b = _hm(st["quiet_from"]), _hm(st["quiet_to"])
    except (KeyError, ValueError, TypeError):
        return None
    if a == b:
        return None
    tz = ZoneInfo(get_settings().local_tz)
    local = now.astimezone(tz)
    t = local.time()
    inside = (a <= t < b) if a < b else (t >= a or t < b)
    if not inside:
        return None
    end = datetime.combine(local.date(), b, tz)
    if end <= local:
        end += timedelta(days=1)
    return end


async def sent(engine: AsyncEngine, key: str) -> bool:
    async with engine.connect() as c:
        return (await c.execute(text("SELECT 1 FROM notify_log WHERE key = :k AND ok"), {"k": key})).first() is not None


async def send(engine: AsyncEngine, kind: str, key: str, title: str, body: str, *, priority: int = 3,
               tags: list[str] | None = None, click: str | None = None, urgent: bool = False,
               client: httpx.AsyncClient | None = None, now: datetime | None = None, force: bool = False) -> dict[str, Any]:  # fmt: skip
    """Send one notification, unless it is switched off or was sent before (by `key`). What happened."""
    now = now or datetime.now(UTC)
    st = await settings(engine)
    if not force and (not st["enabled"] or not st["kinds"].get(kind, False)):
        return {"sent": False, "why": "switched off"}
    if not force and await sent(engine, key):
        return {"sent": False, "why": "sent before"}
    url = get_settings().ntfy_url
    if not url:
        return {"sent": False, "why": "no ntfy server: add docker-compose.tailscale.yml (docs/notifications.md)"}
    headers = {"Title": _ascii(title), "Priority": str(5 if urgent else priority)}
    if tags:
        headers["Tags"] = ",".join(tags)
    if click:
        headers["Click"] = click
    held = None if urgent else quiet_until(st, now)
    if held:
        headers["Delay"] = str(int(held.timestamp()))
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    ok, err = False, None
    try:
        r = await client.post(f"{url.rstrip('/')}/{st['topic']}", content=body.encode("utf-8"), headers=headers)
        ok = r.status_code < 300
        err = None if ok else f"ntfy answered HTTP {r.status_code}: {r.text[:200]}"
    except httpx.HTTPError as exc:
        err = f"ntfy could not be reached ({type(exc).__name__})"
    finally:
        if own:
            await client.aclose()
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO notify_log (key, kind, title, body, sent_at, ok, error) VALUES (:k, :kind, :t, :b, :at, :ok, :e) "
                             "ON CONFLICT (key) DO UPDATE SET sent_at = EXCLUDED.sent_at, ok = EXCLUDED.ok, error = EXCLUDED.error, "
                             "title = EXCLUDED.title, body = EXCLUDED.body"),
                        {"k": key, "kind": kind, "t": title, "b": body, "at": now, "ok": ok, "e": err})  # fmt: skip
    if not ok:
        log.warning("notification %s not sent: %s", key, err)
    return {"sent": ok, "error": err, "held_until": held.isoformat() if held and ok else None}


def _ascii(s: str) -> str:
    """ntfy's Title header is latin-1: anything else (an emoji) is left out."""
    return s.encode("latin-1", "ignore").decode("latin-1")


# --- routes ------------------------------------------------------------------------------------------------------------
def _view(st: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    server = s.ntfy_public_url or None
    return {**st, "kind_names": KINDS, "server": server, "connected": bool(s.ntfy_url),
            "subscribe": f"{server}/{st['topic']}" if server else None}  # fmt: skip


class NotifyIn(BaseModel):
    enabled: bool | None = None
    kinds: dict[str, bool] | None = None
    quiet_from: str | None = Field(None, pattern=r"^\d{1,2}:\d{2}$")
    quiet_to: str | None = Field(None, pattern=r"^\d{1,2}:\d{2}$")
    new_topic: bool = Field(False, description="make a new topic (the phone must subscribe again)")


@router.get("/settings")
async def get_notify(request: Request) -> dict[str, Any]:
    return _view(await settings(request.app.state.engine))


@router.put("/settings")
async def put_notify(request: Request, body: NotifyIn) -> dict[str, Any]:
    st = await settings(request.app.state.engine)
    if body.kinds and set(body.kinds) - set(KINDS):
        raise HTTPException(422, f"kinds are {', '.join(KINDS)}")
    for k in ("quiet_from", "quiet_to"):
        v = getattr(body, k)
        if v is not None:
            try:
                _hm(v)
            except ValueError as exc:
                raise HTTPException(422, f"{k}: a time such as 22:00") from exc
            st[k] = v
    if body.enabled is not None:
        st["enabled"] = body.enabled
    if body.kinds:
        st["kinds"] = {**st["kinds"], **body.kinds}
    if body.new_topic:
        st["topic"] = None
    await _save(request.app.state.engine, st)
    return _view(await settings(request.app.state.engine))


@router.post("/test")
async def test(request: Request) -> dict[str, Any]:
    """A test notification now (quiet hours and the switches do not apply)."""
    now = datetime.now(UTC)
    r = await send(request.app.state.engine, "test", f"test:{now.isoformat()}", "World Assistant",
                   "Notifications work: this phone will hear about what you switched on.", tags=["white_check_mark"],
                   force=True, urgent=True, now=now)  # fmt: skip
    if not r["sent"]:
        raise HTTPException(502, r.get("error") or r.get("why") or "not sent")
    return r


@router.get("/log")
async def get_log(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as c:
        rows = (await c.execute(text("SELECT key, kind, title, body, sent_at, ok, error FROM notify_log ORDER BY sent_at DESC LIMIT 30"))).all()
    return {"sent": [{"key": r.key, "kind": r.kind, "title": r.title, "body": r.body, "at": r.sent_at.isoformat(), "ok": r.ok,
                      "error": r.error} for r in rows]}  # fmt: skip


# --- checking each step, for when nothing arrives ------------------------------------------------------------------


def _step(name: str, ok: bool | None, detail: str) -> dict[str, Any]:
    return {"step": name, "ok": ok, "detail": detail}


async def check_steps(engine: Any, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Every step from World Assistant to the phone that can be checked from here, in order, each passed (True),
    failed (False) or not checkable from the server (None); the first failure is the one to fix."""
    from urllib.parse import urlsplit

    s, st = get_settings(), await settings(engine)
    steps: list[dict[str, Any]] = []
    steps.append(_step("Notifications switched on", bool(st["enabled"]), "on" if st["enabled"] else
                       "Settings → Notifications: tick \"Send notifications\""))  # fmt: skip
    if not s.ntfy_url:
        steps.append(_step("World Assistant knows its ntfy server", False,
                           "no ntfy server: start the stack with docker-compose.tailscale.yml (docs/notifications.md)"))
        return {"steps": steps, "subscribe": None}
    public = (s.ntfy_public_url or "").rstrip("/")
    host = urlsplit(public).hostname or ""
    good_host = host.endswith(".ts.net") and host.count(".") >= 3  # <device>.<tailnet>.ts.net
    steps.append(_step("The phone's address for the server", bool(public) and good_host,
                       f"{public}" if good_host else
                       f"{public or '(none)'}: it should be https://<device>.<your tailnet>.ts.net:8443. Set WA_TAILNET in .env "
                       "to your tailnet's name (the Tailscale admin page, DNS), then update"))  # fmt: skip
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        base = s.ntfy_url.rstrip("/")
        try:
            r = await client.get(f"{base}/v1/health")
            healthy = r.status_code == 200 and (r.json() or {}).get("healthy") is True
            steps.append(_step("World Assistant reaches its ntfy server", healthy, "healthy" if healthy else f"HTTP {r.status_code}"))
        except (httpx.HTTPError, ValueError) as exc:
            steps.append(_step("World Assistant reaches its ntfy server", False, f"{type(exc).__name__}: is the ntfy container running?"))
            return {"steps": steps, "subscribe": None}
        try:
            r = await client.get(f"{base}/v1/config")
            conf = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            conf = {}
        their = (conf.get("base_url") or "").rstrip("/")
        if their:
            same = their == public
            steps.append(_step("The server's own address matches", same, their if same else
                               f"the server calls itself {their}, World Assistant tells the phone {public}: iPhones fetch "
                               "each message from the server's own address, so they must match (WA_TAILNET in .env)"))  # fmt: skip
        topic = st["topic"]
        now = datetime.now(UTC)
        r = await client.post(f"{base}/{topic}", content=b"World Assistant: checking each step to your phone.",
                              headers={"Title": "Checking notifications", "Tags": "mag"})  # fmt: skip
        mid = (r.json() or {}).get("id") if r.status_code < 300 else None
        steps.append(_step("A test message is accepted", bool(mid), f"message {mid}" if mid else f"HTTP {r.status_code}: {r.text[:120]}"))
        if mid:
            r = await client.get(f"{base}/{topic}/json", params={"poll": "1", "since": str(int(now.timestamp()) - 5)})
            kept = mid in r.text
            steps.append(_step("The server holds it for the phone", kept, "yes: a subscribed phone gets it" if kept else
                               "not found when asked for it again (the server's cache is off or full)"))  # fmt: skip
        steps.append(_step("Your phone", None,
                           "Not checkable from here. In the ntfy app: subscribed to exactly the address below, Tailscale on, and "
                           "notifications allowed for ntfy in the iPhone's Settings. A message just went to the topic: if it is "
                           "in the app but made no sound, it is the phone's settings; if it is not in the app, the subscription"))  # fmt: skip
    finally:
        if own:
            await client.aclose()
    return {"steps": steps, "subscribe": f"{public}/{st['topic']}" if public else None}


@router.post("/check")
async def check(request: Request) -> dict[str, Any]:
    """Check every step to the phone that can be checked from the server, in order, and send a test message."""
    return await check_steps(request.app.state.engine)

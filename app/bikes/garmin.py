"""Rides from Garmin Connect's Personal API: each rider's own key, a daily sync, and a probe of what the key can see.

The Personal API is Garmin's self-serve API for your own data (developer.garmin.com/gc-developer-program/personal).
A rider makes a key in Garmin Connect (Settings, Personal API Key) and saves it on the Bikes page; it is kept in the
keys folder as garmin_<rider>.txt and never shown again. The key is swapped for a bearer token lasting up to 24 hours
(there is no refresh token: a new one is asked for each day). Only the paths in Garmin's own documentation and client
are used for the sync: the activity search, an activity's details, its original file, and weight. Nothing is written
to Garmin.

    GET    /v1/bikes/garmin?rider=           the key (set or not, when it expires), the last sync, the bike rules
    PUT    /v1/bikes/garmin/key?rider=       save a key (checked with Garmin first)
    DELETE /v1/bikes/garmin/key?rider=       forget it
    POST   /v1/bikes/garmin/sync?rider=      sync now
    PUT    /v1/bikes/garmin/rules?rider=     which bike each kind of Garmin ride goes to; whether to take your weight

    python -m app.bikes.garmin [RIDER]       the probe: what the key gives (rides, history, gear, GPS, sensors, weight)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import struct
import sys
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import secretstore
from app.auth import require_token
from app.bikes import fit

log = logging.getLogger("wa.bikes.garmin")
router = APIRouter(prefix="/v1/bikes/garmin", tags=["bikes"], dependencies=[Depends(require_token)])

BASE = os.environ.get("WA_GARMIN_BASE_URL", "https://personal.connectapi.garmin.com").rstrip("/")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
SEARCH = "/activitylist-service/activities/search/activities"
PAGE = 100
WARN_DAYS = 14  # warn this long before a key runs out
RECHECK_DAYS = 30  # each daily sync looks this far back again, for rides changed or deleted in Garmin
CYCLING = re.compile(r"cycl|biking|bike|ride|bmx", re.IGNORECASE)
NOT_CYCLING = re.compile(r"motor|run|walk", re.IGNORECASE)
INDOOR = re.compile(r"indoor|virtual|trainer", re.IGNORECASE)


class GarminError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# --- the key ---------------------------------------------------------------------------------------------------------
def key_name(rider: str) -> str:
    return "garmin_" + rider.replace("-", "_")


def read_key(rider: str) -> str | None:
    """The rider's key: saved from the Bikes page (keys folder), or from the vault or a secret file like any other."""
    name = key_name(rider)
    saved = secretstore._file_value(name, secretstore.keys_dir())
    if saved:
        return saved
    from app.config import SECRETS_DIR

    value, source = secretstore.resolve(name, SECRETS_DIR)
    return value if source not in ("missing", "default") and value else None


def riders_with_keys() -> list[str]:
    d = secretstore.keys_dir()
    try:
        files = sorted(p.name for p in d.iterdir() if p.name.startswith("garmin_") and not p.name.startswith("."))
    except OSError:
        return []
    return [re.sub(r"\.txt$", "", f)[7:].replace("_", "-") for f in files]


# --- the API ---------------------------------------------------------------------------------------------------------
_tokens: dict[str, tuple[str, float]] = {}  # sha256 of the key: (token, expires at)


def _expiry(token: str) -> float:
    try:
        part = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return time.time() + 23 * 3600  # not a JWT after all: assume the documented day


class Garmin:
    """One rider's Garmin, through their key."""

    def __init__(self, key: str, client: httpx.AsyncClient) -> None:
        self.key, self.client = key.strip(), client
        self.fp = hashlib.sha256(self.key.encode()).hexdigest()
        self.key_expires: datetime | None = None  # known when a token runs out before its day: the key ends then
        self.limits: dict[str, str] = {}  # any rate-limit headers seen

    async def token(self, fresh: bool = False) -> str:
        got = _tokens.get(self.fp)
        if got and not fresh and got[1] - time.time() > 3600:
            return got[0]
        r = await self.client.post(BASE + TOKEN_PATH, headers={"Accept": "*/*", "Authorization": f"ApiKey {self.key}"})
        if r.status_code in (401, 403):
            raise GarminError("Garmin refused the key: it may have expired or been deleted. Make a new one in Garmin "
                              "Connect (Settings, Personal API Key) and save it here", r.status_code)  # fmt: skip
        if r.status_code >= 400:
            raise GarminError(f"Garmin's token service answered HTTP {r.status_code}", r.status_code)
        tok = r.text.strip().strip('"')
        if not tok:
            raise GarminError("Garmin's token service sent back nothing")
        exp = _expiry(tok)
        if exp - time.time() < 23 * 3600:  # the token stops early only because the key does
            self.key_expires = datetime.fromtimestamp(exp, UTC)
        _tokens[self.fp] = (tok, exp)
        return tok

    async def get(self, path: str, params: dict[str, Any] | None = None, accept: str = "application/json") -> httpx.Response:
        for attempt in (1, 2):
            r = await self.client.get(BASE + path, params=params,
                                      headers={"Accept": accept, "Authorization": f"Bearer {await self.token(attempt == 2)}"})  # fmt: skip
            self.limits.update({k: v for k, v in r.headers.items() if "rate" in k.lower() or "limit" in k.lower()})
            if r.status_code == 401 and attempt == 1:
                continue  # the token may have been withdrawn: one more go with a new one
            return r
        return r

    async def json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        r = await self.get(path, params)
        if r.status_code == 429:
            raise GarminError("Garmin says too many requests for now: the sync tries again tomorrow", 429)
        if r.status_code >= 400:
            raise GarminError(f"Garmin answered HTTP {r.status_code} for {path.split('?')[0]}", r.status_code)
        return r.json() if r.content else None

    async def activities(self, since: date | None = None, pages: int = 100) -> list[dict[str, Any]]:
        """Every activity (newest first), back to `since`."""
        out: list[dict[str, Any]] = []
        for n in range(pages):
            params: dict[str, Any] = {"start": n * PAGE, "limit": PAGE}
            if since:
                params |= {"startDate": since.isoformat(), "endDate": (date.today() + timedelta(days=1)).isoformat()}
            got = await self.json(SEARCH, params)
            batch = got.get("activityList", []) if isinstance(got, dict) else (got or [])
            out += batch
            if len(batch) < PAGE:
                break
            await asyncio.sleep(0.5)
        return out

    async def weight(self, days: int = 120) -> float | None:
        """The latest weight in kg over the last `days`, if any is logged."""
        end = date.today()
        got = await self.json(f"/weight-service/weight/range/{(end - timedelta(days=days)).isoformat()}/{end.isoformat()}",
                              {"includeAll": "true"})  # fmt: skip
        return latest_weight(got)


def latest_weight(got: Any) -> float | None:
    """The newest weight in a weight-service answer. Garmin gives grams; anything under 400 is taken as kg already."""
    best: tuple[str, float] | None = None

    def walk(x: Any, when: str = "") -> None:
        nonlocal best
        if isinstance(x, dict):
            w = when or str(x.get("summaryDate") or x.get("calendarDate") or x.get("date") or x.get("timestampGMT") or "")
            v = x.get("weight")
            if isinstance(v, int | float) and v > 0:
                kg = v / 1000 if v > 400 else float(v)
                if 20 < kg < 250 and (best is None or w >= best[0]):
                    best = (w, round(kg, 1))
            for k, y in x.items():
                if k != "weight":
                    walk(y, w)
        elif isinstance(x, list):
            for y in x:
                walk(y, when)

    walk(got)
    return best[1] if best else None


# --- an activity as a ride -------------------------------------------------------------------------------------------
def type_key(a: dict[str, Any]) -> str:
    t = a.get("activityType") or a.get("activityTypeDTO") or {}
    return str(t.get("typeKey") if isinstance(t, dict) else t or "")


def is_ride(a: dict[str, Any]) -> bool:
    k = type_key(a)
    return bool(CYCLING.search(k)) and not NOT_CYCLING.search(k)


def _started(a: dict[str, Any]) -> datetime | None:
    if isinstance(a.get("beginTimestamp"), int | float):
        return datetime.fromtimestamp(a["beginTimestamp"] / 1000, UTC)
    for k, tz in (("startTimeGMT", UTC), ("startTimeLocal", None)):
        v = a.get(k)
        if v:
            try:
                d = datetime.fromisoformat(str(v).replace(" ", "T"))
            except ValueError:
                continue
            if d.tzinfo is None:
                from app.bikes.api import TZ

                d = d.replace(tzinfo=tz or TZ)
            return d.astimezone(UTC)
    return None


def to_ride(a: dict[str, Any], rider: str) -> dict[str, Any] | None:
    """A Garmin activity as a ride here, or None if it is not a ride."""
    if not is_ride(a) or a.get("activityId") is None:
        return None
    started = _started(a)
    if started is None:
        return None
    k = type_key(a)
    commute = is_commute(a)
    activity = k.replace("_", " ").title() + (" (Commute)" if commute and "commut" not in k else "")
    secs = a.get("movingDuration") or a.get("duration") or a.get("elapsedDuration") or 0
    lat, lon = a.get("startLatitude"), a.get("startLongitude")
    return {"id": f"garmin:{a['activityId']}", "rider": rider, "started": started,
            "km": round(float(a.get("distance") or 0) / 1000, 2), "hours": round(float(secs) / 3600, 3),
            "indoor": bool(INDOOR.search(k)) or bool(a.get("indoor")),
            "lat": float(lat) if isinstance(lat, int | float) else None, "lon": float(lon) if isinstance(lon, int | float) else None,
            "activity": activity, "type_key": k, "commute": commute, "title": a.get("activityName") or None,
            "source": "garmin"}  # fmt: skip


COMMUTE = re.compile(r"commut|transport", re.IGNORECASE)


def is_commute(a: dict[str, Any]) -> bool:
    """Garmin's Commute: as the ride's type, or its event type (Transportation, shown as Commute)."""
    ev = a.get("eventType") or a.get("eventTypeDTO") or {}
    ev = ev.get("typeKey") if isinstance(ev, dict) else ev
    return bool(COMMUTE.search(type_key(a)) or COMMUTE.search(str(ev or "")))


def rule_for(rules: dict[str, Any], type_key_: str, commute: bool) -> Any:
    """The bike a ride goes to: Commute first (it says most), then its kind of ride, then any other."""
    return (rules.get("commute") if commute else None) or rules.get(type_key_) or rules.get("default")


def same_ride(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """A ride imported from the CSV (or logged by hand) that is this Garmin ride: within five minutes and 2%."""
    close = abs((a["started"] - b["started"]).total_seconds()) <= 300
    return close and abs(a["km"] - b["km"]) <= max(0.2, 0.02 * max(a["km"], b["km"]))


# --- the sync --------------------------------------------------------------------------------------------------------
def _settings(rider_row: dict[str, Any]) -> dict[str, Any]:
    return dict((rider_row.get("settings") or {}).get("garmin") or {})


async def _save_settings(engine: AsyncEngine, rider: str, g: dict[str, Any]) -> None:
    async with engine.begin() as c:
        await c.execute(text("UPDATE bike_rider SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{garmin}', "
                             "CAST(:g AS jsonb)) WHERE id = :r"), {"g": json.dumps(g, default=str), "r": rider})  # fmt: skip


async def sync(engine: AsyncEngine, rider: str, client: httpx.AsyncClient | None = None, full: bool = False,
               now: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """Bring the rider's Garmin rides in: new ones added (to the bike their kind of ride goes to), ones changed in
    Garmin updated, ones deleted there taken off, CSV rides that are the same ride swapped for the Garmin one (keeping
    what was said about them), and the weight taken. The first sync reads the whole history; later ones the last
    month. What happened is kept with the rider for the page."""
    from app.bikes.api import _rider, _suggest_wet, insert_rides
    from app.wishlist import _Req

    now = now or datetime.now(UTC)
    row = await _rider(engine, rider)
    g = _settings(row)
    key = read_key(rider)
    if not key:
        return {"skipped": "no Garmin key saved for this rider"}
    first = full or not g.get("last_sync")
    since = None if first else (now - timedelta(days=RECHECK_DAYS)).date()
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    result: dict[str, Any] = {"at": now.isoformat(), "full": first}
    try:
        api = Garmin(key, client)
        acts = await api.activities(since)
        weight = None
        if g.get("weight", True):
            try:
                weight = await api.weight()
            except (GarminError, httpx.HTTPError, ValueError) as exc:
                result["weight_error"] = str(exc)
        if api.key_expires:
            g["key_expires"] = api.key_expires.date().isoformat()
    except (GarminError, httpx.HTTPError) as exc:
        g |= {"error": str(exc) if isinstance(exc, GarminError) else f"Garmin could not be reached ({type(exc).__name__})",
              "error_at": now.isoformat()}  # fmt: skip
        await _save_settings(engine, rider, g)
        raise GarminError(g["error"], getattr(exc, "status", None)) from exc
    finally:
        if own:
            await client.aclose()
    rides = [r for a in acts if (r := to_ride(a, rider))]
    seen_types = sorted({r["type_key"] for r in rides} | set(g.get("types") or []))
    rules = {k: v for k, v in (g.get("rules") or {}).items() if v}
    async with engine.connect() as c:
        bikes = {r.id: r for r in await c.execute(text("SELECT id, retired_at FROM bike WHERE rider_id = :r"), {"r": rider})}
        have = {r.id: dict(r._mapping) for r in await c.execute(text("SELECT * FROM bike_ride WHERE rider_id = :r"), {"r": rider})}
    others = [h for h in have.values() if h["source"] != "garmin"]
    new, changed, twins = [], [], []
    for r in rides:
        old = have.get(r["id"])
        if old:
            if any(old[k] != r[k] for k in ("km", "hours", "title", "activity", "indoor")):
                changed.append(r)
            continue
        twin = next((h for h in others if same_ride(h, r)), None)
        if twin:  # the same ride from the CSV or by hand: keep what was said about it
            others.remove(twin)
            twins.append(twin["id"])
            r |= {k: twin[k] for k in ("bike_id", "wet", "wet_suggested", "wet_why", "bike_checked")}
        else:
            rule = rule_for(rules, r["type_key"], r["commute"])
            bike = rule if rule in bikes and not bikes[rule].retired_at else None
            long_past = now - r["started"] > timedelta(days=28)
            r |= {"bike_id": bike, "wet": None, "bike_checked": long_past}  # long past: not worth asking which bike
        new.append(r)
    gone: list[str] = []
    if rides:  # rides deleted in Garmin, within what was read this time
        floor = datetime.combine(since, datetime.min.time(), UTC) if since else min(x["started"] for x in rides)
        ids = {r["id"] for r in rides}
        gone = [h["id"] for h in have.values() if h["source"] == "garmin" and h["id"] not in ids and h["started"] >= floor]
    fresh = [r for r in new if r.get("wet") is None and not r["indoor"] and now - r["started"] < timedelta(days=27)]
    if fresh:
        try:
            await _suggest_wet(_Req(engine), fresh)
        except Exception:
            log.warning("no wet suggestions for %s's new rides", rider, exc_info=True)
    for r in rides:
        r.pop("type_key", None)
        r.pop("commute", None)
    async with engine.begin() as c:
        for i in twins + gone:
            await c.execute(text("DELETE FROM bike_ride WHERE id = :i"), {"i": i})
        for r in changed:
            await c.execute(text("UPDATE bike_ride SET km = :km, hours = :hours, title = :title, activity = :activity, "
                                 "indoor = :indoor WHERE id = :id"), {k: r[k] for k in ("id", "km", "hours", "title", "activity", "indoor")})  # fmt: skip
        added = await insert_rides(c, new) - len(twins)
        if weight:
            await c.execute(text("UPDATE bike_rider SET weight_kg = :w WHERE id = :r"), {"w": weight, "r": rider})
    result |= {"activities": len(acts), "rides": len(rides), "added": added, "updated": len(changed),
               "from_csv": len(twins), "removed": len(gone), "weight_kg": weight}  # fmt: skip
    g |= {"last_sync": now.isoformat(), "last_result": result, "types": seen_types, "error": None}
    await _save_settings(engine, rider, g)
    return result


async def garmin_sync(ctx: dict[str, Any]) -> Any:
    """The worker's daily sync, for every rider with a key."""
    from app.jobs import tracked

    engine = ctx["engine"]

    async def run() -> Any:
        out: dict[str, Any] = {}
        async with engine.connect() as c:
            riders = [r.id for r in await c.execute(text("SELECT id FROM bike_rider ORDER BY created"))]
        for rider in riders:
            if not read_key(rider):
                continue
            try:
                out[rider] = await sync(engine, rider)
            except GarminError as exc:
                out[rider] = {"error": str(exc)}
        return out

    return await tracked(engine, "garmin_sync", run)


# --- the routes ------------------------------------------------------------------------------------------------------
def status(rider_row: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    g = _settings(rider_row)
    today = today or date.today()
    exp = g.get("key_expires")
    days = (date.fromisoformat(exp) - today).days if exp else None
    warn = None
    if days is not None and days < 0:
        warn = "Your Garmin key has run out: make a new one in Garmin Connect (Settings, Personal API Key) and save it here"
    elif days is not None and days <= WARN_DAYS:
        warn = f"Your Garmin key runs out in {days} day{'s' if days != 1 else ''} ({exp}): make a new one in Garmin Connect"
    return {"key_set": bool(read_key(rider_row["id"])), "key_expires": exp, "days_left": days, "warn": warn,
            "last_sync": g.get("last_sync"), "last_result": g.get("last_result"), "error": g.get("error"),
            "error_at": g.get("error_at"), "rules": g.get("rules") or {},
            "types": ["commute"] + [t for t in g.get("types") or [] if t != "commute"],
            "weight": g.get("weight", True)}  # fmt: skip


@router.get("")
async def get_status(request: Request, rider: str = Query(...)) -> dict[str, Any]:
    from app.bikes.api import _rider

    return status(await _rider(request.app.state.engine, rider))


class KeyIn(BaseModel):
    value: str = Field(min_length=8, max_length=4096)
    expires: date | None = Field(None, description="when the key runs out, as chosen when it was made (optional)")


@router.put("/key")
async def save_key(request: Request, body: KeyIn, rider: str = Query(...)) -> dict[str, Any]:
    """Check the key with Garmin, then keep it in the keys folder. A key Garmin refuses is not kept."""
    from app.bikes.api import _rider
    from app.keys import write_key

    engine = request.app.state.engine
    row = await _rider(engine, rider)
    checked = True
    async with httpx.AsyncClient(timeout=30) as client:
        api = Garmin(body.value, client)
        try:
            await api.token(fresh=True)
        except GarminError as exc:
            if exc.status in (401, 403):
                raise HTTPException(422, str(exc)) from exc
            checked = False
        except httpx.HTTPError:
            checked = False
    write_key(key_name(rider), body.value)
    g = _settings(row) | {"error": None}
    if body.expires:
        g["key_expires"] = body.expires.isoformat()
    elif api.key_expires:
        g["key_expires"] = api.key_expires.date().isoformat()
    await _save_settings(engine, rider, g)
    return {"saved": True, "checked": checked,
            "message": "Saved, and Garmin accepted it" if checked else "Saved; Garmin could not be reached to check it"}  # fmt: skip


@router.delete("/key")
async def forget_key(request: Request, rider: str = Query(...)) -> dict[str, Any]:
    from app.bikes.api import _rider

    row = await _rider(request.app.state.engine, rider)
    for p in (secretstore.keys_dir() / f"{key_name(rider)}.txt", secretstore.keys_dir() / key_name(rider)):
        if p.exists():
            p.unlink()
    g = _settings(row)
    g.pop("key_expires", None)
    await _save_settings(request.app.state.engine, rider, g)
    return {"key_set": bool(read_key(rider))}


@router.post("/sync")
async def sync_now(request: Request, rider: str = Query(...), full: bool = False) -> dict[str, Any]:
    try:
        return await sync(request.app.state.engine, rider, full=full)
    except GarminError as exc:
        raise HTTPException(502 if exc.status != 429 else 429, str(exc)) from exc


class RulesIn(BaseModel):
    rules: dict[str, int | None] = Field(default_factory=dict, description="Garmin activity type (or 'default'): bike id")
    weight: bool | None = None
    apply: bool = Field(False, description="also give rides with no bike yet the bike their rule says")


@router.put("/rules")
async def put_rules(request: Request, body: RulesIn, rider: str = Query(...)) -> dict[str, Any]:
    from app.bikes.api import _rider

    engine = request.app.state.engine
    row = await _rider(engine, rider)
    async with engine.connect() as c:
        mine = {r.id for r in await c.execute(text("SELECT id FROM bike WHERE rider_id = :r"), {"r": rider})}
    bad = [v for v in body.rules.values() if v is not None and v not in mine]
    if bad:
        raise HTTPException(422, f"Not one of this rider's bikes: {bad}")
    g = _settings(row)
    g["rules"] = {k: v for k, v in body.rules.items() if v is not None}
    if body.weight is not None:
        g["weight"] = body.weight
    await _save_settings(engine, rider, g)
    given = 0
    if body.apply and g["rules"]:
        async with engine.begin() as c:
            for r in await c.execute(text("SELECT id, activity FROM bike_ride WHERE rider_id = :r AND bike_id IS NULL AND source = 'garmin'"),
                                     {"r": rider}):  # fmt: skip
                act = (r.activity or "").lower()
                k = act.replace(" (commute)", "").replace(" ", "_")
                b = rule_for(g["rules"], k, "commut" in act)
                if b:
                    given += (await c.execute(text("UPDATE bike_ride SET bike_id = :b WHERE id = :i"), {"b": b, "i": r.id})).rowcount
    return {**status({**row, "settings": {**(row.get("settings") or {}), "garmin": g}}), "given": given}


# --- the probe -------------------------------------------------------------------------------------------------------
LOOK = re.compile(r"gear|bike|indoor|tire|tyre|pressure|batter|device|sensor|manufact|di2|axs|shift|power", re.IGNORECASE)


def _interesting(d: Any, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(d, dict):
        for k, v in d.items():
            name = f"{prefix}{k}"
            if LOOK.search(str(k)):
                out.append(f"{name} = {json.dumps(v, default=str)[:160]}")
            elif isinstance(v, dict | list):
                out += _interesting(v, name + ".")
    elif isinstance(d, list):
        for i, v in enumerate(d[:3]):
            out += _interesting(v, f"{prefix}[{i}].")
    return out


async def probe(rider: str) -> bool:
    """What the key gives, printed: True if the key works and rides can be read."""
    key = read_key(rider)
    print(f"Rider {rider}: key {'saved' if key else 'NOT saved'}")
    if not key:
        return True
    found: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=60) as client:
        api = Garmin(key, client)
        try:
            tok = await api.token(fresh=True)
        except (GarminError, httpx.HTTPError) as exc:
            print(f"FAILED: token: {exc}")
            return False
        left = (_tokens[api.fp][1] - time.time()) / 3600
        print(f"Token: OK, lasts {left:.1f} h" + (f"; the key itself runs out {api.key_expires:%d %B %Y}" if api.key_expires else ""))
        del tok

        try:
            acts = await api.activities()
        except (GarminError, httpx.HTTPError) as exc:
            print(f"FAILED: activities: {exc}")
            return False
        types: dict[str, int] = {}
        for a in acts:
            types[type_key(a)] = types.get(type_key(a), 0) + 1
        rides = [a for a in acts if is_ride(a)]
        dates = sorted(d for a in rides if (d := _started(a)))
        print(f"\nActivities: {len(acts)} in all, {len(rides)} rides" +
              (f", rides from {dates[0]:%d %b %Y} to {dates[-1]:%d %b %Y}" if dates else ""))  # fmt: skip
        print("  by type: " + ", ".join(f"{k or '?'} {n}" for k, n in sorted(types.items(), key=lambda x: -x[1])))
        evs: dict[str, int] = {}
        for a in rides:
            ev = a.get("eventType") or {}
            k = str(ev.get("typeKey") if isinstance(ev, dict) else ev or "none")
            evs[k] = evs.get(k, 0) + 1
        print("  rides by event type: " + ", ".join(f"{k} {n}" for k, n in sorted(evs.items(), key=lambda x: -x[1])))
        print(f"  commutes: {sum(is_commute(a) for a in rides)}")
        found["history_from"] = dates[0].date().isoformat() if dates else None
        found["indoor marked"] = any(INDOOR.search(type_key(a)) for a in rides) or any("indoor" in a for a in rides)
        if not rides:
            print("No rides to look at further.")
        else:
            a = rides[0]
            aid = a["activityId"]
            print(f"\nLatest ride {aid} ({type_key(a)}, {_started(a):%d %b %Y}): summary fields:\n  " + ", ".join(sorted(a)))
            for line in _interesting(a):
                print("  * " + line)
            r = await api.get(f"/activity-service/activity/{aid}")
            print(f"\nActivity details: HTTP {r.status_code}")
            if r.status_code == 200 and r.content:
                d = r.json()
                print("  fields: " + ", ".join(sorted(d)) if isinstance(d, dict) else f"  a {type(d).__name__}")
                for line in _interesting(d)[:40]:
                    print("  * " + line)
            gear_any = False
            for path, params in (("/gear-service/gear/filterGear", {"activityId": aid}),
                                 (f"/gear-service/gear/activity/{aid}", None)):  # fmt: skip
                g = await api.get(path, params)
                body = g.text[:300].replace("\n", " ") if g.status_code == 200 else ""
                print(f"\nGear ({path}): HTTP {g.status_code} {body}")
                gear_any = gear_any or (g.status_code == 200 and bool(g.content) and g.text.strip() not in ("[]", "null"))
            found["gear"] = gear_any or any("gear" in x.lower() for x in _interesting(a))
            print("(Changing a ride's bike is not tried: the probe only reads.)")

            outdoor = next((x for x in rides if not INDOOR.search(type_key(x))), a)
            f = await api.get(f"/download-service/files/activity/{outdoor['activityId']}",
                              accept="application/zip, application/x-zip-compressed, application/octet-stream")  # fmt: skip
            print(f"\nOriginal file for {outdoor['activityId']}: HTTP {f.status_code}, {len(f.content)} bytes")
            if f.status_code == 200 and f.content:
                try:
                    name, data = fit.from_zip(f.content)
                    s = fit.summarise(data)
                    print(f"  {name}: " + ", ".join(f"{k} {n}" for k, n in sorted(s.counts.items(), key=lambda x: -x[1])[:14]))
                    print(f"  GPS points: {len(s.track)}")
                    for dv in s.devices:
                        print(f"  device: {json.dumps({k: v for k, v in dv.items() if v is not None})}")
                    for df in s.dev_fields:
                        print(f"  developer field: {json.dumps(df)}")
                    found["gps"] = bool(s.track)
                    found["tyre pressure"] = any(re.search(r"press|tire|tyre", str(d.get("name")), re.IGNORECASE) for d in s.dev_fields)
                    found["device batteries"] = any(d.get("battery_status") or d.get("battery_v") or d.get("battery_level")
                                                    for d in s.devices if d.get("index") not in (0, None))  # fmt: skip
                except (fit.FitError, ValueError, KeyError, struct.error) as exc:
                    print(f"  could not be read: {type(exc).__name__}: {exc}")

        try:
            w = await api.weight(365)
            print(f"\nWeight: {'latest ' + str(w) + ' kg' if w else 'none logged in the last year'}")
            found["weight"] = bool(w)
        except (GarminError, httpx.HTTPError, ValueError) as exc:
            print(f"\nWeight: {exc}")
            found["weight"] = False
        if api.limits:
            print("\nRate-limit headers: " + json.dumps(api.limits))
    print("\nWhat the key gives: " + ", ".join(f"{k}: {'yes' if v is True else 'no' if v is False else v}" for k, v in found.items()))
    return True


def main(argv: list[str]) -> int:
    riders = argv or riders_with_keys()
    if not riders:
        print("No Garmin key saved yet (Bikes page, or keys/garmin_<rider>.txt): nothing to probe")
        return 0
    ok = True
    for r in riders:
        ok = asyncio.run(probe(r)) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main(sys.argv[1:]))

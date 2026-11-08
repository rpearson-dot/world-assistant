"""The bike garage's API: riders, bikes (with their default parts), labelled sets, parts, swaps, rides, the service
log, the due list, batteries, the spares shelf, running cost, and due parts onto the Outdoors list.

    GET  /v1/bikes/catalogue                     bike types, choices, kinds of part and each type's default answers
    GET  /v1/bikes/riders | POST                 riders (anyone can add themselves)
    GET  /v1/bikes?rider=                        everything for a rider: bikes, sets, parts with wear, due list,
                                                 batteries, shelf, kit, rides to confirm, pressures, costs
    POST /v1/bikes/bikes | PATCH /{id}           add a bike (its parts come from its answers) or change it
    POST /v1/bikes/sets | PATCH /{id}            a labelled set (a wheel, or any group of parts)
    POST /v1/bikes/parts | PATCH /{id}           add a part (fitted, on the shelf, or rider kit) or change it
    PUT  /v1/bikes/{bikes|parts}/{id}/photo      a photo (a data: URL); GET it back
    POST /v1/bikes/fit                           move a part or set (or every wheel of a group) to a bike, a set, or off
    POST /v1/bikes/service                       log a replacement, service, check, charge, top-up, cost or crash
    POST /v1/bikes/rides | PATCH /{id}           a ride by hand, or say whether it was wet / which bike
    POST /v1/bikes/rides/import                  Garmin Connect's activities CSV export
    POST /v1/bikes/rides/bulk                    many rides at once: all dry, all bikes right, bikes by kind of ride
    POST /v1/bikes/parts/{id}/to-list            put a part on the Outdoors list
    GET|PUT /v1/bikes/settings?rider=            to the Outdoors list automatically, wet factor, weight...
    POST /v1/bikes/lookup                        a product page's name, brand and picture
    GET  /v1/bikes/costs?rider=                  running cost per bike and per kind of part

Nothing is ever bought.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth import require_token
from app.bikes import defaults as D
from app.bikes import wear as W
from app.bikes.rain import home_point, suggest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/bikes", tags=["bikes"], dependencies=[Depends(require_token)])
TZ = ZoneInfo("Europe/London")
BIKE_KG = 10.0  # a bike's weight when none is given (for tyre pressures)
SETTINGS = {"to_outdoors": "auto", "wet_factor": W.WET_FACTOR, "trainer_has_cassette": True}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def _now() -> datetime:
    return datetime.now(UTC)


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, bytes | memoryview):
        return None
    return v


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


def _out(d: dict[str, Any]) -> dict[str, Any]:
    return {k: _plain(v) for k, v in d.items() if k != "photo"}


async def _rider(engine: AsyncEngine, rider: str) -> dict[str, Any]:
    async with engine.connect() as c:
        r = (await c.execute(text("SELECT * FROM bike_rider WHERE id = :r"), {"r": rider})).first()
    if not r:
        raise HTTPException(404, f"No rider {rider!r}: add yourself on the Bikes page")
    return _row(r)


def describe(p: dict[str, Any]) -> str:
    """A part's brand, model and model details in a few words: "SRAM X-Sync 40t", "Hope 180 mm Centre Lock"."""
    d = p.get("details") or {}
    bits = [str(x) for x in (p.get("brand"), p.get("model")) if x]
    for key, _ in D.SPECS.get(p["kind"], []):
        v = d.get(key)
        if v in (None, "", False):
            continue
        if v is True:  # a yes/no detail: its name ("Tubeless"), not "True"
            bits.append(dict(D.SPECS[p["kind"]]).get(key, key).split(" (")[0])
            continue
        unit = {"teeth": "t", "diameter_mm": " mm", "width_mm": " mm", "travel_mm": " mm", "ml": " ml", "speeds": "-speed"}.get(key, "")
        bits.append(f"{v}{unit}" if unit else str(v))
    return " ".join(bits)


def name_of(p: dict[str, Any]) -> str:
    """A part's own label, or its kind (and end) when it has none."""
    if p.get("label"):
        return str(p["label"])
    k = D.KINDS.get(p["kind"], D.KINDS["other"]).name
    pos = p.get("position")
    return f"{k} ({pos})" if pos else k


# --- the catalogue ---------------------------------------------------------------------------------------------------
@router.get("/catalogue")
async def catalogue() -> dict[str, Any]:
    """Bike types, the choices for each answer, each kind of part with its default life, and each type's defaults."""
    kinds = {k: {"name": v.name, "km": v.km, "hours": v.hours, "days": v.days, "on": v.on, "wet": v.wet,
                 "indoor": v.indoor, "measure": v.measure, "icon": v.icon, "action": v.action, "bought": v.bought}
             for k, v in D.KINDS.items()}  # fmt: skip
    from app.bikes import art
    from app.bikes.art import MODELS
    from app.themes import PAINTS

    return {"types": D.BIKE_TYPES, "choices": D.CHOICES,
            "models": {k: {"name": m["name"], "type": m["type"]} for k, m in MODELS.items()},
            "paints": {k: v[0] for k, v in PAINTS.items()}, "kinds": kinds, "icons": art.catalogue_icons(), "specs": D.SPECS,
            "defaults": {t: D.defaults_for(t) for t in D.BIKE_TYPES}}  # fmt: skip


# --- riders ----------------------------------------------------------------------------------------------------------
class RiderIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    weight_kg: float | None = Field(None, gt=20, lt=250)


@router.get("/riders")
async def riders(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as c:
        rows = [_out(_row(r)) for r in await c.execute(text("SELECT id, name, weight_kg FROM bike_rider ORDER BY created"))]
    return {"riders": rows}


@router.post("/riders", status_code=201)
async def add_rider(request: Request, body: RiderIn) -> dict[str, Any]:
    rid = _slug(body.name)
    if not rid:
        raise HTTPException(422, "Give a name with a letter or number in it")
    async with request.app.state.engine.begin() as c:
        if (await c.execute(text("SELECT 1 FROM bike_rider WHERE id = :r"), {"r": rid})).first():
            raise HTTPException(409, f"There is already a rider called {body.name!r}")
        await c.execute(text("INSERT INTO bike_rider (id, name, weight_kg) VALUES (:r, :n, :w)"),
                        {"r": rid, "n": body.name.strip(), "w": body.weight_kg})  # fmt: skip
    return {"id": rid, "name": body.name.strip()}


class SettingsIn(BaseModel):
    to_outdoors: Literal["auto", "tap", "never"] | None = None
    wet_factor: float | None = Field(None, ge=1, le=3)
    trainer_has_cassette: bool | None = None
    weight_kg: float | None = Field(None, gt=20, lt=250)


@router.get("/settings")
async def get_settings_(request: Request, rider: str = Query(...)) -> dict[str, Any]:
    r = await _rider(request.app.state.engine, rider)
    return {**SETTINGS, **(r["settings"] or {}), "weight_kg": r["weight_kg"]}


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn, rider: str = Query(...)) -> dict[str, Any]:
    r = await _rider(request.app.state.engine, rider)
    st = {**(r["settings"] or {}), **{k: v for k, v in body.model_dump().items() if v is not None and k != "weight_kg"}}
    async with request.app.state.engine.begin() as c:
        await c.execute(text("UPDATE bike_rider SET settings = CAST(:s AS jsonb), weight_kg = COALESCE(:w, weight_kg) WHERE id = :r"),
                        {"s": json.dumps(st), "w": body.weight_kg, "r": rider})  # fmt: skip
    return await get_settings_(request, rider)


# --- bikes, sets and parts -------------------------------------------------------------------------------------------
class BikeIn(BaseModel):
    rider: str
    label: str = Field(min_length=1, max_length=60)
    type: str = "road"
    ebike: bool = False
    config: dict[str, Any] = Field(default_factory=dict, description="answers that differ from the type's defaults")
    fitted_at: date | None = Field(None, description="when its current parts were fitted (default today)")
    weight_kg: float | None = Field(None, gt=3, lt=80)


async def _fit(c: Any, item: str, item_id: int, at: datetime, bike_id: int | None = None, set_id: int | None = None) -> None:
    await c.execute(text("INSERT INTO bike_fit (item, item_id, bike_id, set_id, at) VALUES (:i, :n, :b, :s, :t)"),
                    {"i": item, "n": item_id, "b": bike_id, "s": set_id, "t": at})  # fmt: skip


async def _check_place(c: Any, part: dict[str, Any], bike_id: int | None, set_id: int | None) -> None:
    """Refuse (422) a part going where it cannot: a cassette on a front wheel, a chain on a wheel, a rotor on a
    rim-brake wheel, a second tyre on a wheel (app/bikes/placement.py)."""
    from app.bikes import placement as P

    if bike_id is None and set_id is None:
        return
    now = _now()
    bike = wheel = wheel_bike = None
    if bike_id is not None:
        r = (await c.execute(text("SELECT id, label, config FROM bike WHERE id = :b"), {"b": bike_id})).first()
        if not r:
            raise HTTPException(404, "No such bike")
        bike = _row(r)
    if set_id is not None:
        r = (await c.execute(text("SELECT id, label, kind, position FROM bike_set WHERE id = :s"), {"s": set_id})).first()
        if not r:
            raise HTTPException(404, "No such wheel or set")
        wheel = _row(r)
        on = (await c.execute(text("SELECT bike_id FROM bike_fit WHERE item = 'set' AND item_id = :s AND at <= :t "
                                   "ORDER BY at DESC, id DESC LIMIT 1"), {"s": set_id, "t": now})).scalar()  # fmt: skip
        if on is not None:
            r = (await c.execute(text("SELECT id, label, config FROM bike WHERE id = :b"), {"b": on})).first()
            wheel_bike = _row(r) if r else None
    # what is fitted there now (each part's latest fit), other than this part
    here = [_row(r) for r in await c.execute(text(
        "SELECT p.id, p.kind, p.position FROM bike_part p JOIN LATERAL (SELECT bike_id, set_id FROM bike_fit f WHERE "
        "f.item = 'part' AND f.item_id = p.id AND f.at <= :t ORDER BY at DESC, id DESC LIMIT 1) f ON true "
        "WHERE p.status IN ('fitted', 'kit') AND " + ("f.set_id = :w" if set_id is not None else "f.bike_id = :w AND f.set_id IS NULL")),
        {"t": now, "w": set_id if set_id is not None else bike_id})]  # fmt: skip
    why = P.problems(part, bike=bike if set_id is None else None, wheel=wheel, wheel_bike=wheel_bike, there=here)
    if why:
        raise HTTPException(422, "; ".join(why))


async def _new_part(c: Any, rider: str, kind: str, position: str | None, details: dict[str, Any], at: datetime | None,
                    status: str = "fitted", **more: Any) -> int:  # fmt: skip
    return (await c.execute(text(
        "INSERT INTO bike_part (rider_id, kind, position, details, fitted_at, status, label, brand, model, url, photo_url, "
        "price, quantity, start_km, start_hours, life, notes) VALUES (:r, :k, :p, CAST(:d AS jsonb), :t, :st, :label, :brand, "
        ":model, :url, :photo_url, :price, :q, :skm, :sh, CAST(:life AS jsonb), :notes) RETURNING id"),
        {"r": rider, "k": kind, "p": position, "d": json.dumps(details), "t": at, "st": status,
         "label": more.get("label"), "brand": more.get("brand"), "model": more.get("model"), "url": more.get("url"),
         "photo_url": more.get("photo_url"), "price": more.get("price"), "q": more.get("quantity") or 1,
         "skm": more.get("start_km") or 0, "sh": more.get("start_hours") or 0,
         "life": json.dumps(more["life"]) if more.get("life") else None, "notes": more.get("notes")})).scalar_one()  # fmt: skip


def _check(cfg: dict[str, Any]) -> None:
    from app.bikes.art import MODELS
    from app.themes import PAINTS

    for k, allowed in D.CHOICES.items():
        if k in cfg and cfg[k] not in allowed:
            raise HTTPException(422, f"{k} must be one of {', '.join(allowed)}")
    if cfg.get("model") and cfg["model"] not in MODELS:
        raise HTTPException(422, f"model must be one of {', '.join(MODELS)}")
    for k in ("paint", "paint2", "pinstripe"):
        if cfg.get(k) and cfg[k] not in PAINTS:
            raise HTTPException(422, f"{k} must be one of {', '.join(PAINTS)}")


@router.post("/bikes", status_code=201)
async def add_bike(request: Request, body: BikeIn) -> dict[str, Any]:
    """A bike with the parts its answers give it: the type's defaults, with any answers given changed. Its wheels are
    two labelled sets ("<bike> front wheel", "<bike> rear wheel"), so they can be swapped and renamed."""
    engine = request.app.state.engine
    await _rider(engine, body.rider)
    try:
        cfg = {**D.defaults_for(body.type, body.ebike), **body.config, "type": body.type, "ebike": body.ebike}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _check(cfg)
    at = datetime.combine(body.fitted_at, datetime.min.time(), TZ) if body.fitted_at else _now()
    async with engine.begin() as c:
        if (await c.execute(text("SELECT 1 FROM bike WHERE rider_id = :r AND label = :l"), {"r": body.rider, "l": body.label})).first():
            raise HTTPException(409, f"You already have a bike called {body.label!r}")
        bid = (await c.execute(text("INSERT INTO bike (rider_id, label, config, weight_kg) VALUES (:r, :l, CAST(:c AS jsonb), :w) "
                                    "RETURNING id"), {"r": body.rider, "l": body.label, "c": json.dumps(cfg), "w": body.weight_kg})).scalar_one()  # fmt: skip
        wheels = {}
        for end in ("front", "rear"):
            label = await _free_label(c, body.rider, "bike_set", f"{body.label} {end} wheel")
            sid = (await c.execute(text("INSERT INTO bike_set (rider_id, label, kind, position, grp) VALUES (:r, :l, 'wheel', :p, :g) "
                                        "RETURNING id"), {"r": body.rider, "l": label, "p": end, "g": f"{body.label} wheels"})).scalar_one()  # fmt: skip
            await _fit(c, "set", sid, at, bike_id=bid)
            wheels[end] = sid
        for p in D.planned_parts(cfg):
            pid = await _new_part(c, body.rider, p.kind, p.position, p.details, at)
            if p.on == "bike":
                await _fit(c, "part", pid, at, bike_id=bid)
            else:
                await _fit(c, "part", pid, at, set_id=wheels[p.on])
    return {"id": bid, "label": body.label, "config": cfg, "wheels": wheels}


async def _free_label(c: Any, rider: str, table: str, want: str) -> str:
    label, n = want, 2
    while (await c.execute(text(f"SELECT 1 FROM {table} WHERE rider_id = :r AND label = :l"), {"r": rider, "l": label})).first():
        label, n = f"{want} {n}", n + 1
    return label


class BikePatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=60)
    config: dict[str, Any] | None = Field(None, description="changed answers: parts are added or retired to match")
    weight_kg: float | None = Field(None, gt=3, lt=80)
    records: dict[str, Any] | None = None
    photo_url: str | None = Field(None, max_length=500)
    garmin_gear: str | None = None
    retired: bool | None = None


@router.patch("/bikes/{bike_id}")
async def change_bike(request: Request, bike_id: int, body: BikePatch) -> dict[str, Any]:
    """Change a bike. Changed answers (a rear shock added, the front brake changed) add the parts the new answers
    give and retire the ones they no longer give; the retired ones' history stays in the log."""
    engine = request.app.state.engine
    async with engine.begin() as c:
        b = (await c.execute(text("SELECT * FROM bike WHERE id = :b"), {"b": bike_id})).first()
        if not b:
            raise HTTPException(404, "No such bike")
        b = _row(b)
        sets_ = {"label": body.label, "weight_kg": body.weight_kg, "photo_url": body.photo_url, "garmin_gear": body.garmin_gear}
        for k, v in sets_.items():
            if v is not None:
                await c.execute(text(f"UPDATE bike SET {k} = :v WHERE id = :b"), {"v": v, "b": bike_id})
        if body.records is not None:
            await c.execute(text("UPDATE bike SET records = CAST(:v AS jsonb) WHERE id = :b"), {"v": json.dumps(body.records), "b": bike_id})
        if body.retired is not None:
            await c.execute(text("UPDATE bike SET retired_at = CASE WHEN :r THEN now() ELSE NULL END WHERE id = :b"),
                            {"r": body.retired, "b": bike_id})  # fmt: skip
        changes: dict[str, list[str]] = {"added": [], "retired": []}
        if body.config:
            cfg = {**b["config"], **body.config}
            _check(cfg)
            await c.execute(text("UPDATE bike SET config = CAST(:c AS jsonb) WHERE id = :b"), {"c": json.dumps(cfg), "b": bike_id})
            changes = await _reconcile(c, b, cfg)
    return {"id": bike_id, **changes}


@router.delete("/bikes/{bike_id}")
async def delete_bike(request: Request, bike_id: int) -> dict[str, Any]:
    """Delete a bike added by mistake, with the parts and wheels on it. A bike with rides is retired instead (sold,
    stolen, written off), which keeps its rides, parts, history and cost."""
    async with request.app.state.engine.begin() as c:
        b = (await c.execute(text("SELECT * FROM bike WHERE id = :b"), {"b": bike_id})).first()
        if not b:
            raise HTTPException(404, "No such bike")
        if (await c.execute(text("SELECT count(*) FROM bike_ride WHERE bike_id = :b"), {"b": bike_id})).scalar():
            raise HTTPException(409, "This bike has rides: retire it instead, which keeps its history")
        sets_ = [r.id for r in await c.execute(text(
            "SELECT s.id FROM bike_set s JOIN LATERAL (SELECT bike_id FROM bike_fit f WHERE f.item = 'set' AND f.item_id = s.id "
            "ORDER BY at DESC, id DESC LIMIT 1) f ON true WHERE f.bike_id = :b"), {"b": bike_id})]  # fmt: skip
        parts = [p["id"] for p in await _parts_on(c, bike_id, b.rider_id)]
        for pid in parts:
            await c.execute(text("DELETE FROM bike_fit WHERE item = 'part' AND item_id = :p"), {"p": pid})
            await c.execute(text("DELETE FROM bike_part WHERE id = :p"), {"p": pid})
        for sid in sets_:
            await c.execute(text("DELETE FROM bike_fit WHERE item = 'set' AND item_id = :s"), {"s": sid})
            await c.execute(text("DELETE FROM bike_set WHERE id = :s"), {"s": sid})
        await c.execute(text("DELETE FROM bike WHERE id = :b"), {"b": bike_id})
    return {"id": bike_id, "deleted": True, "parts": len(parts), "sets": len(sets_)}


async def _reconcile(c: Any, b: dict[str, Any], cfg: dict[str, Any]) -> dict[str, list[str]]:
    """Add the parts the new answers give that the bike lacks; retire those it has that they no longer give."""
    now = _now()
    want = [(p.kind, p.position, p.on, p.details) for p in D.planned_parts(cfg)]
    have = await _parts_on(c, b["id"], b["rider_id"])
    wheels = {r.position: r.id for r in await c.execute(text(
        "SELECT s.id, s.position FROM bike_set s JOIN LATERAL (SELECT bike_id FROM bike_fit f WHERE f.item = 'set' AND "
        "f.item_id = s.id ORDER BY at DESC, id DESC LIMIT 1) f ON true WHERE f.bike_id = :b AND s.kind = 'wheel'"), {"b": b["id"]})}  # fmt: skip
    added, retired = [], []
    remaining = list(have)
    for kind, pos, on, details in want:
        hit = next((h for h in remaining if h["kind"] == kind and h["position"] == pos), None)
        if hit:
            remaining.remove(hit)
            continue
        if on != "bike" and on not in wheels:
            continue
        pid = await _new_part(c, b["rider_id"], kind, pos, details, now)
        await _fit(c, "part", pid, now, **({"bike_id": b["id"]} if on == "bike" else {"set_id": wheels[on]}))
        added.append(name_of({"kind": kind, "position": pos}))
    for h in remaining:  # only kinds the answers give: a part of another kind you added yourself stays
        if h["kind"] in _ANY_DEFAULT:
            await c.execute(text("UPDATE bike_part SET status = 'retired', retired_at = :t WHERE id = :p"), {"t": now, "p": h["id"]})
            await _fit(c, "part", h["id"], now)
            retired.append(name_of(h))
    return {"added": added, "retired": retired}


_ANY_DEFAULT = {p.kind for t in D.BIKE_TYPES for cfg in (
    {**D.defaults_for(t), **x} for x in ({}, {"front_brake": "cable_hydraulic", "rear_brake": "drum", "rear_suspension": "post",
                                          "front_suspension": "stem", "drive": "belt"},
                                         {"shifting": "axs", "drive": "hub", "ebike": True, "front_brake": "v_brake"},
                                         {"shifting": "di2", "drive": "single", "rear_suspension": "shock", "dropper": "cable",
                                          "front_tyre": "tubeless", "rear_tyre": "latex"})) for p in D.planned_parts(cfg)}  # fmt: skip


async def _parts_on(c: Any, bike_id: int, rider: str) -> list[dict[str, Any]]:
    """The fitted parts on a bike now: directly, or on a set fitted to it."""
    rows = [_row(r) for r in await c.execute(text("SELECT * FROM bike_part WHERE rider_id = :r AND status = 'fitted'"), {"r": rider})]
    fits = [_row(r) for r in await c.execute(text("SELECT f.* FROM bike_fit f JOIN bike_part p ON f.item = 'part' AND f.item_id = p.id "
                                                  "WHERE p.rider_id = :r UNION ALL SELECT f.* FROM bike_fit f JOIN bike_set s ON "
                                                  "f.item = 'set' AND f.item_id = s.id WHERE s.rider_id = :r"), {"r": rider})]  # fmt: skip
    pl = W.Placer(fits)
    now = _now() + timedelta(seconds=1)
    return [p for p in rows if pl.bike_of_part(p["id"], now) == bike_id]


class SetIn(BaseModel):
    rider: str
    label: str = Field(min_length=1, max_length=60)
    kind: Literal["wheel", "group"] = "group"
    position: Literal["front", "rear"] | None = None
    group: str | None = Field(None, max_length=60, description="wheels sharing a group label swap together")
    bike_id: int | None = Field(None, description="fitted to this bike now (a wheel takes the one it replaces off)")
    parts: bool = Field(False, description="a wheel: give it the usual parts (tyre, rotor, cassette...) for parts_from")
    parts_from: int | None = Field(None, description="the bike whose answers say what the wheel carries (default bike_id)")
    fitted_at: date | None = None


async def _wheels_off(c: Any, bike_id: int, ends: set[str], keep: list[int], at: datetime) -> list[str]:
    """Take a bike's wheels at these ends off (into storage), except those in keep; their labels."""
    off = []
    for r in await c.execute(text(
            "SELECT s.id, s.label FROM bike_set s JOIN LATERAL (SELECT bike_id FROM bike_fit f WHERE f.item = 'set' AND "
            "f.item_id = s.id ORDER BY at DESC, id DESC LIMIT 1) f ON true WHERE f.bike_id = :b AND s.kind = 'wheel' "
            "AND s.position = ANY(:e) AND NOT (s.id = ANY(:mine))"), {"b": bike_id, "e": list(ends), "mine": keep}):  # fmt: skip
        await _fit(c, "set", r.id, at)
        off.append(r.label)
    return off


@router.post("/sets", status_code=201)
async def add_set(request: Request, body: SetIn) -> dict[str, Any]:
    """A labelled set: a wheel (front or rear) or any group of parts. A new wheel can come with the usual parts for a
    bike (from its answers: tyre, tube or sealant, tyre check, rotor, cassette, hub...), and be fitted straight away, the
    wheel it replaces going into storage."""
    if body.kind == "wheel" and not body.position:
        raise HTTPException(422, "Say whether the wheel is a front or a rear one")
    await _rider(request.app.state.engine, body.rider)
    at = datetime.combine(body.fitted_at, datetime.min.time(), TZ) if body.fitted_at else _now()
    made: list[str] = []
    off: list[str] = []
    async with request.app.state.engine.begin() as c:
        if (await c.execute(text("SELECT 1 FROM bike_set WHERE rider_id = :r AND label = :l"), {"r": body.rider, "l": body.label})).first():
            raise HTTPException(409, f"You already have a set called {body.label!r}")
        sid = (await c.execute(text("INSERT INTO bike_set (rider_id, label, kind, position, grp) VALUES (:r, :l, :k, :p, :g) RETURNING id"),
                               {"r": body.rider, "l": body.label, "k": body.kind, "p": body.position, "g": body.group or None})).scalar_one()  # fmt: skip
        if body.bike_id is not None and body.kind == "wheel":
            off = await _wheels_off(c, body.bike_id, {body.position or ""}, [sid], at)
        await _fit(c, "set", sid, at, bike_id=body.bike_id)
        source = body.parts_from or body.bike_id
        if body.parts and body.kind == "wheel" and source is not None:
            row = (await c.execute(text("SELECT config FROM bike WHERE id = :b AND rider_id = :r"), {"b": source, "r": body.rider})).first()
            if not row:
                raise HTTPException(404, "No such bike to take the wheel's parts from")
            for pl in D.planned_parts(row.config or {}):
                if pl.on == body.position:
                    pid = await _new_part(c, body.rider, pl.kind, pl.position, pl.details, at)
                    await _fit(c, "part", pid, at, set_id=sid)
                    made.append(D.KINDS.get(pl.kind, D.KINDS["other"]).name)
    return {"id": sid, "label": body.label, "parts": made, "took_off": off}


class SetPatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=60)
    group: str | None = Field(None, max_length=60)
    retired: bool | None = None


@router.patch("/sets/{set_id}")
async def change_set(request: Request, set_id: int, body: SetPatch) -> dict[str, Any]:
    async with request.app.state.engine.begin() as c:
        if body.label:
            await c.execute(text("UPDATE bike_set SET label = :v WHERE id = :s"), {"v": body.label, "s": set_id})
        if body.group is not None:
            await c.execute(text("UPDATE bike_set SET grp = NULLIF(:v, '') WHERE id = :s"), {"v": body.group, "s": set_id})
        if body.retired is not None:
            await c.execute(text("UPDATE bike_set SET retired_at = CASE WHEN :r THEN now() END WHERE id = :s"), {"r": body.retired, "s": set_id})
    return {"id": set_id}


class PartIn(BaseModel):
    rider: str
    kind: str
    position: str | None = Field(None, max_length=30)
    label: str | None = Field(None, max_length=60)
    brand: str | None = Field(None, max_length=60)
    model: str | None = Field(None, max_length=120)
    url: str | None = Field(None, max_length=500)
    photo_url: str | None = Field(None, max_length=500)
    price: float | None = Field(None, ge=0)
    status: Literal["fitted", "shelf", "kit"] = "fitted"
    quantity: int = Field(1, ge=1, le=99)
    bike_id: int | None = None
    set_id: int | None = None
    fitted_at: date | None = None
    start_km: float = Field(0, ge=0)
    start_hours: float = Field(0, ge=0)
    life: dict[str, float] | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = Field(None, max_length=500)


@router.post("/parts", status_code=201)
async def add_part(request: Request, body: PartIn) -> dict[str, Any]:
    """A part: fitted (to a bike or a set), on the shelf (a spare, with how many), or rider kit (a helmet, shoes, an AXS
    battery)."""
    if body.kind not in D.KINDS:
        raise HTTPException(422, f"No kind of part {body.kind!r}")
    await _rider(request.app.state.engine, body.rider)
    at = datetime.combine(body.fitted_at, datetime.min.time(), TZ) if body.fitted_at else _now()
    async with request.app.state.engine.begin() as c:
        if body.status != "shelf":
            await _check_place(c, {"id": None, "kind": body.kind, "position": body.position}, body.bike_id, body.set_id)
        pid = await _new_part(c, body.rider, body.kind, body.position, body.details, at if body.status != "shelf" else None,
                              body.status, **body.model_dump(exclude={"rider", "kind", "position", "details", "status", "fitted_at"}))  # fmt: skip
        if body.status != "shelf":
            await _fit(c, "part", pid, at, bike_id=body.bike_id, set_id=body.set_id)
    return {"id": pid}


class PartPatch(BaseModel):
    label: str | None = Field(None, max_length=60)
    brand: str | None = Field(None, max_length=60)
    model: str | None = Field(None, max_length=120)
    url: str | None = Field(None, max_length=500)
    photo_url: str | None = Field(None, max_length=500)
    price: float | None = Field(None, ge=0)
    quantity: int | None = Field(None, ge=0, le=99)
    fitted_at: date | None = None
    start_km: float | None = Field(None, ge=0)
    start_hours: float | None = Field(None, ge=0)
    life: dict[str, float] | None = Field(None, description="{} goes back to the kind's default")
    details: dict[str, Any] | None = None
    notes: str | None = Field(None, max_length=500)


@router.patch("/parts/{part_id}")
async def change_part(request: Request, part_id: int, body: PartPatch) -> dict[str, Any]:
    vals = body.model_dump(exclude_unset=True)
    async with request.app.state.engine.begin() as c:
        if not (await c.execute(text("SELECT 1 FROM bike_part WHERE id = :p"), {"p": part_id})).first():
            raise HTTPException(404, "No such part")
        for k, v in vals.items():
            if k in ("life", "details"):
                await c.execute(text(f"UPDATE bike_part SET {k} = CAST(:v AS jsonb) WHERE id = :p"),
                                {"v": json.dumps(v) if v else None if k == "life" else "{}", "p": part_id})  # fmt: skip
            elif k == "fitted_at":
                await c.execute(text("UPDATE bike_part SET fitted_at = :v WHERE id = :p"),
                                {"v": datetime.combine(v, datetime.min.time(), TZ) if v else None, "p": part_id})  # fmt: skip
            else:
                await c.execute(text(f"UPDATE bike_part SET {k} = :v WHERE id = :p"), {"v": v, "p": part_id})
    return {"id": part_id, "changed": sorted(vals)}


class PhotoIn(BaseModel):
    data_url: str = Field(max_length=3_000_000, description="data:image/jpeg;base64,... (the page shrinks it first)")


@router.delete("/parts/{part_id}")
async def delete_part(request: Request, part_id: int) -> dict[str, Any]:
    """Delete a part added by mistake, with its log. A part that was used and is done with is replaced or retired
    instead, which keeps its history and cost."""
    async with request.app.state.engine.begin() as c:
        await c.execute(text("DELETE FROM bike_fit WHERE item = 'part' AND item_id = :p"), {"p": part_id})
        if not (await c.execute(text("DELETE FROM bike_part WHERE id = :p"), {"p": part_id})).rowcount:
            raise HTTPException(404, "No such part")
    return {"id": part_id, "deleted": True}


async def _put_photo(request: Request, table: str, item_id: int, body: PhotoIn) -> dict[str, Any]:
    head, _, data = body.data_url.partition(",")
    if head not in ("data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64") or not data:
        raise HTTPException(422, "Send a JPEG, PNG or WebP as a data: URL")
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise HTTPException(422, "That photo's data is not base64") from exc
    async with request.app.state.engine.begin() as c:
        n = (await c.execute(text(f"UPDATE {table} SET photo = :b WHERE id = :i"), {"b": raw, "i": item_id})).rowcount
    if not n:
        raise HTTPException(404, "Not found")
    return {"id": item_id, "bytes": len(raw)}


async def _get_photo(request: Request, table: str, item_id: int) -> Response:
    async with request.app.state.engine.connect() as c:
        raw = (await c.execute(text(f"SELECT photo FROM {table} WHERE id = :i"), {"i": item_id})).scalar()
    if not raw:
        raise HTTPException(404, "No photo")
    kind = "png" if raw[:4] == b"\x89PNG" else "webp" if raw[8:12] == b"WEBP" else "jpeg"
    return Response(bytes(raw), media_type=f"image/{kind}", headers={"Cache-Control": "private, max-age=3600"})


@router.put("/parts/{part_id}/photo")
async def put_part_photo(request: Request, part_id: int, body: PhotoIn) -> dict[str, Any]:
    return await _put_photo(request, "bike_part", part_id, body)


@router.get("/parts/{part_id}/photo")
async def part_photo(request: Request, part_id: int) -> Response:
    return await _get_photo(request, "bike_part", part_id)


@router.put("/bikes/{bike_id}/photo")
async def put_bike_photo(request: Request, bike_id: int, body: PhotoIn) -> dict[str, Any]:
    return await _put_photo(request, "bike", bike_id, body)


@router.get("/bikes/{bike_id}/photo")
async def bike_photo(request: Request, bike_id: int) -> Response:
    return await _get_photo(request, "bike", bike_id)


# --- moving things ---------------------------------------------------------------------------------------------------
class FitIn(BaseModel):
    item: Literal["part", "set", "group"]
    id: int | None = Field(None, description="the part or set; for a group, use group")
    group: str | None = Field(None, description="every wheel with this group label (a wheelset)")
    rider: str | None = None
    bike_id: int | None = Field(None, description="onto this bike (none, with no set_id: off, onto the shelf or kit)")
    set_id: int | None = Field(None, description="a part into this set")
    at: datetime | None = Field(None, description="when (default now); rides after this count on the new place")


@router.post("/fit")
async def fit(request: Request, body: FitIn) -> dict[str, Any]:
    """Move a part or a set, or every wheel of a group: onto a bike, into a set (a part), or off. Swapping a wheelset onto
    a bike takes the wheels it replaces off that bike. A past `at` re-counts the rides since then (until the next move)."""
    at = body.at or _now()
    if at.tzinfo is None:
        at = at.replace(tzinfo=TZ)
    moved: list[str] = []
    async with request.app.state.engine.begin() as c:
        if body.item == "group":
            if not body.group or not body.rider:
                raise HTTPException(422, "Give the rider and the group label")
            sets_ = [_row(r) for r in await c.execute(text("SELECT * FROM bike_set WHERE rider_id = :r AND grp = :g AND retired_at IS NULL"),
                                                      {"r": body.rider, "g": body.group})]  # fmt: skip
            if not sets_:
                raise HTTPException(404, f"No wheels in a group called {body.group!r}")
        elif body.item == "set":
            row = (await c.execute(text("SELECT * FROM bike_set WHERE id = :s"), {"s": body.id})).first()
            if not row:
                raise HTTPException(404, "No such set")
            sets_ = [_row(row)]
        else:
            row = (await c.execute(text("SELECT * FROM bike_part WHERE id = :p"), {"p": body.id})).first()
            if not row:
                raise HTTPException(404, "No such part")
            p = _row(row)
            await _check_place(c, p, body.bike_id, body.set_id)
            if p["status"] == "shelf" and (p["quantity"] or 1) > 1 and (body.bike_id or body.set_id):  # one off a pile
                await c.execute(text("UPDATE bike_part SET quantity = quantity - 1 WHERE id = :p"), {"p": p["id"]})
                p = {**p, "id": await _new_part(c, p["rider_id"], p["kind"], p["position"], p["details"] or {}, at, label=p["label"],
                                                brand=p["brand"], model=p["model"], url=p["url"], photo_url=p["photo_url"],
                                                price=p["price"], life=p["life"])}  # fmt: skip
            await _fit(c, "part", p["id"], at, bike_id=body.bike_id, set_id=body.set_id)
            kit = D.KINDS.get(p["kind"], D.KINDS["other"]).on == "rider"  # rider kit (an AXS battery) stays kit on a bike
            status = "kit" if kit else ("fitted" if (body.bike_id or body.set_id) else "shelf")
            await c.execute(text("UPDATE bike_part SET status = :s, fitted_at = CASE WHEN :s = 'fitted' AND status <> 'fitted' "
                                 "THEN :t ELSE fitted_at END WHERE id = :p"), {"s": status, "t": at, "p": p["id"]})  # fmt: skip
            return {"moved": [name_of(p)], "at": at.isoformat()}
        if body.bike_id is not None:  # the wheels these replace come off
            ends = {s["position"] for s in sets_ if s["kind"] == "wheel"}
            moved += [f"{x} off" for x in await _wheels_off(c, body.bike_id, ends, [s["id"] for s in sets_], at)]
        warnings: list[str] = []
        if body.bike_id is not None:  # a wheel's parts that do not suit the bike it goes onto: said, not refused
            from app.bikes import placement as P

            br = (await c.execute(text("SELECT id, label, config FROM bike WHERE id = :b"), {"b": body.bike_id})).first()
            for s in sets_:
                parts = [_row(r) for r in await c.execute(text(
                    "SELECT p.id, p.kind, p.position FROM bike_part p JOIN LATERAL (SELECT set_id FROM bike_fit f WHERE "
                    "f.item = 'part' AND f.item_id = p.id ORDER BY at DESC, id DESC LIMIT 1) f ON true "
                    "WHERE f.set_id = :s AND p.status = 'fitted'"), {"s": s["id"]})]  # fmt: skip
                if br:
                    warnings += P.wheel_warnings(s, parts, _row(br))
        for s in sets_:
            await _fit(c, "set", s["id"], at, bike_id=body.bike_id)
            moved.append(s["label"])
    return {"moved": moved, "at": at.isoformat(), "warnings": warnings}


# --- the service log -------------------------------------------------------------------------------------------------
RESETS = {"replaced", "serviced", "checked", "charged", "topped_up", "bled"}  # measured_ok is only noted: wear goes on


class ServiceIn(BaseModel):
    rider: str
    action: Literal["replaced", "serviced", "checked", "charged", "topped_up", "bled", "measured_ok", "cost", "note", "crash"]
    part_id: int | None = None
    bike_id: int | None = None
    at: datetime | None = None
    cost: float | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=500)
    level_before: float | None = Field(None, ge=0, le=100, description="a battery's charge (%) when put on charge")
    spare_id: int | None = Field(None, description="replaced with this spare from the shelf")
    new: dict[str, Any] | None = Field(None, description="replaced with a new part: brand, model, price, label")


@router.post("/service")
async def service(request: Request, body: ServiceIn) -> dict[str, Any]:
    """Log what was done. A replacement retires the old part and fits the new one (a spare from the shelf, taking one
    off it, or a new part) in the same place, with its count from now. A service, check, charge or top-up restarts the
    part's count. A crash puts its checks on the due list. A cost or a note is just kept."""
    at = body.at or _now()
    async with request.app.state.engine.begin() as c:
        part = None
        if body.part_id is not None:
            row = (await c.execute(text("SELECT * FROM bike_part WHERE id = :p AND rider_id = :r"), {"p": body.part_id, "r": body.rider})).first()
            if not row:
                raise HTTPException(404, "No such part for this rider")
            part = _row(row)
        if body.action in RESETS and part is None:
            raise HTTPException(422, "Say which part")
        sid = (await c.execute(text("INSERT INTO bike_service (rider_id, part_id, bike_id, at, action, resets, level_before, cost, note) "
                                    "VALUES (:r, :p, :b, :t, :a, :x, :l, :c, :n) RETURNING id"),
                               {"r": body.rider, "p": body.part_id, "b": body.bike_id, "t": at, "a": body.action,
                                "x": body.action in RESETS and body.action != "replaced", "l": body.level_before,
                                "c": body.cost, "n": body.note})).scalar_one()  # fmt: skip
        out: dict[str, Any] = {"id": sid, "action": body.action}
        if body.action == "replaced" and part:
            where = (await c.execute(text("SELECT bike_id, set_id FROM bike_fit WHERE item = 'part' AND item_id = :p ORDER BY at DESC, id DESC LIMIT 1"),
                                     {"p": part["id"]})).first()  # fmt: skip
            await c.execute(text("UPDATE bike_part SET status = 'retired', retired_at = :t WHERE id = :p"), {"t": at, "p": part["id"]})
            await _fit(c, "part", part["id"], at)
            if body.spare_id:
                spare = (await c.execute(text("SELECT * FROM bike_part WHERE id = :s AND status = 'shelf' AND quantity > 0"),
                                         {"s": body.spare_id})).first()  # fmt: skip
                if not spare:
                    raise HTTPException(409, "That spare is not on the shelf")
                spare = _row(spare)
                if spare["quantity"] > 1:  # one off the shelf: a copy of it is fitted
                    await c.execute(text("UPDATE bike_part SET quantity = quantity - 1 WHERE id = :s"), {"s": spare["id"]})
                    new_id = await _new_part(c, body.rider, part["kind"], part["position"], {**part["details"], **(spare["details"] or {})}, at,
                                             label=spare["label"], brand=spare["brand"], model=spare["model"], url=spare["url"],
                                             photo_url=spare["photo_url"], price=spare["price"], life=part["life"])  # fmt: skip
                else:
                    new_id = spare["id"]
                    await c.execute(text("UPDATE bike_part SET status = 'fitted', fitted_at = :t, kind = :k, position = :p, "
                                         "life = COALESCE(life, CAST(:life AS jsonb)) WHERE id = :s"),
                                    {"t": at, "k": part["kind"], "p": part["position"], "s": new_id,
                                     "life": json.dumps(part["life"]) if part["life"] else None})  # fmt: skip
                left = (await c.execute(text("SELECT COALESCE(SUM(quantity), 0) FROM bike_part WHERE rider_id = :r AND status = 'shelf' "
                                             "AND kind = :k AND quantity > 0 AND id <> :n"), {"r": body.rider, "k": part["kind"], "n": new_id})).scalar()  # fmt: skip
                out["spares_left"] = int(left or 0)
            else:
                # the same again (nothing new said: its brand, model and details carried over), or something different
                n = body.new or {}
                other = any(n.get(k) for k in ("brand", "model", "url"))
                details = {**(part["details"] or {}), **(n.get("details") or {})}
                new_id = await _new_part(c, body.rider, part["kind"], part["position"], details, at,
                                         label=n.get("label", part["label"]), brand=n.get("brand", part["brand"]),
                                         model=n.get("model", part["model"]),
                                         url=n.get("url", None if other else part["url"]),
                                         photo_url=n.get("photo_url", None if other else part["photo_url"]),
                                         price=n.get("price", body.cost if body.cost is not None else None if other else part["price"]),
                                         life=n.get("life", part["life"]))  # fmt: skip
                if not body.note:
                    was, now_ = describe(part), describe({"kind": part["kind"], "brand": n.get("brand", part["brand"]),
                                                          "model": n.get("model", part["model"]), "details": details})  # fmt: skip
                    note = f"{was} → {now_}" if was != now_ else f"the same again ({was})" if was else None
                    await c.execute(text("UPDATE bike_service SET note = :n WHERE id = :s"), {"n": note, "s": sid})
                    out["note"] = note
            await _fit(c, "part", new_id, at, bike_id=where.bike_id if where else None, set_id=where.set_id if where else None)
            await c.execute(text("UPDATE bike_service SET new_part_id = :n, spare_id = :sp WHERE id = :s"),
                            {"n": new_id, "sp": body.spare_id or None, "s": sid})  # fmt: skip
            out["new_part"] = new_id
    return out


@router.delete("/service/{service_id}")
async def undo_service(request: Request, service_id: int) -> dict[str, Any]:
    """Undo a logged service, check, charge or cost: the part's count goes back to what it was. Undoing a replacement
    also puts the old part back where it was and takes the new one away (a spare goes back on the shelf). A
    replacement whose new part has had things logged since is refused: undo those first."""
    async with request.app.state.engine.begin() as c:
        s = (await c.execute(text("SELECT * FROM bike_service WHERE id = :s"), {"s": service_id})).first()
        if not s:
            raise HTTPException(404, "No such entry")
        s = _row(s)
        out: dict[str, Any] = {"id": service_id, "action": s["action"], "undone": True}
        if s["action"] == "replaced" and s.get("part_id"):
            new_id = s.get("new_part_id")
            status = "fitted"
            if new_id is not None:
                later = (await c.execute(text("SELECT count(*) FROM bike_service WHERE part_id = :n"), {"n": new_id})).scalar()
                if later:
                    raise HTTPException(409, "The new part has had things logged since: undo those first")
                new = _row((await c.execute(text("SELECT * FROM bike_part WHERE id = :n"), {"n": new_id})).first())
                status = new["status"] if new["status"] in ("fitted", "kit") else "fitted"
                if s.get("spare_id") == new_id:  # the spare itself was fitted: back on the shelf
                    await c.execute(text("DELETE FROM bike_fit WHERE item = 'part' AND item_id = :n AND at >= :t"), {"n": new_id, "t": s["at"]})
                    await c.execute(text("UPDATE bike_part SET status = 'shelf', fitted_at = NULL WHERE id = :n"), {"n": new_id})
                    out["back_on_shelf"] = new_id
                else:
                    if s.get("spare_id"):  # one was taken off a pile of spares
                        await c.execute(text("UPDATE bike_part SET quantity = quantity + 1 WHERE id = :sp"), {"sp": s["spare_id"]})
                        out["back_on_shelf"] = s["spare_id"]
                    await c.execute(text("DELETE FROM bike_fit WHERE item = 'part' AND item_id = :n"), {"n": new_id})
                    await c.execute(text("DELETE FROM bike_part WHERE id = :n"), {"n": new_id})
                    out["removed_part"] = new_id
            # the old part: off the bike at the replacement, so that fit goes, and it is fitted again
            await c.execute(text("DELETE FROM bike_fit WHERE item = 'part' AND item_id = :p AND at = :t AND bike_id IS NULL AND set_id IS NULL"),
                            {"p": s["part_id"], "t": s["at"]})  # fmt: skip
            await c.execute(text("UPDATE bike_part SET status = :st, retired_at = NULL WHERE id = :p"), {"st": status, "p": s["part_id"]})
            out["restored_part"] = s["part_id"]
        await c.execute(text("DELETE FROM bike_service WHERE id = :s"), {"s": service_id})
    return out


# --- rides -----------------------------------------------------------------------------------------------------------
class RideIn(BaseModel):
    rider: str
    bike_id: int | None = None
    started: datetime
    km: float = Field(ge=0, le=2000)
    hours: float = Field(0, ge=0, le=72)
    indoor: bool = False
    wet: bool | None = None
    title: str | None = Field(None, max_length=120)
    lat: float | None = None
    lon: float | None = None


async def _suggest_wet(request: Request, rides: list[dict[str, Any]]) -> None:
    """Fill in each outdoor ride's wet suggestion from the rain gauges (where it was, or home)."""
    async with request.app.state.engine.connect() as c:
        places = [_row(r) for r in await c.execute(text("SELECT id, kind, lat, lon FROM place"))]
    home = home_point(places)
    now = _now()
    async with httpx.AsyncClient(timeout=10) as client:
        for r in rides:
            if r["indoor"] or r.get("wet") is not None:
                continue
            pt = (r["lat"], r["lon"]) if r.get("lat") is not None and r.get("lon") is not None else home
            if not pt:
                r["wet_why"] = "no place saved to look up the rain for"
                continue
            r["wet_suggested"], r["wet_why"] = await suggest(client, pt[0], pt[1], r["started"], r["hours"], now)


async def _save_rides(request: Request, rides: list[dict[str, Any]]) -> int:
    async with request.app.state.engine.begin() as c:
        return await insert_rides(c, rides)


async def insert_rides(c: Any, rides: list[dict[str, Any]]) -> int:
    """Add rides (one already here is left as it is); how many were new."""
    n = 0
    for r in rides:
        n += (await c.execute(text(
            "INSERT INTO bike_ride (id, rider_id, bike_id, started, km, hours, indoor, wet, wet_suggested, wet_why, lat, lon, "
            "activity, title, source, garmin_bike, bike_checked) VALUES (:id, :rider, :bike_id, :started, :km, :hours, :indoor, "
            ":wet, :wet_suggested, :wet_why, :lat, :lon, :activity, :title, :source, :garmin_bike, :bike_checked) "
            "ON CONFLICT (id) DO NOTHING"),
            {"wet_suggested": None, "wet_why": None, "lat": None, "lon": None, "activity": None, "title": None,
             "garmin_bike": None, "bike_checked": False, **r})).rowcount  # fmt: skip
    return n


@router.post("/rides", status_code=201)
async def add_ride(request: Request, body: RideIn) -> dict[str, Any]:
    """A ride by hand. Outdoors, it is given a wet suggestion from the rain at the time, to confirm."""
    await _rider(request.app.state.engine, body.rider)
    started = body.started if body.started.tzinfo else body.started.replace(tzinfo=TZ)
    r = {**body.model_dump(exclude={"rider", "started"}), "rider": body.rider, "started": started,
         "id": f"manual:{body.rider}:{started.isoformat()}:{body.km}", "source": "manual", "activity": None,
         "bike_checked": body.bike_id is not None}  # chosen by hand: no need to ask  # fmt: skip
    await _suggest_wet(request, [r])
    await _save_rides(request, [r])
    return {"id": r["id"], "wet_suggested": r.get("wet_suggested"), "wet_why": r.get("wet_why")}


class RidePatch(BaseModel):
    wet: bool | None = None
    bike_id: int | None = Field(None, description="the bike it was really on (this confirms it)")
    bike_checked: bool | None = Field(None, description="true: the bike it has is right")


@router.patch("/rides/{ride_id:path}")
async def change_ride(request: Request, ride_id: str, body: RidePatch) -> dict[str, Any]:
    """Say whether a ride was wet (the suggestion is only that), or which bike it was on (Garmin often keeps the last
    bike chosen): either confirms it."""
    vals = body.model_dump(exclude_unset=True)
    if "bike_id" in vals:
        vals["bike_checked"] = True
    async with request.app.state.engine.begin() as c:
        for k, v in vals.items():
            n = (await c.execute(text(f"UPDATE bike_ride SET {k} = :v WHERE id = :i"), {"v": v, "i": ride_id})).rowcount
            if not n:
                raise HTTPException(404, "No such ride")
    return {"id": ride_id, **vals}


class BulkIn(BaseModel):
    rider: str
    action: Literal["dry", "bikes_ok", "bike_by_kind"]
    keep_suggested_wet: bool = Field(True, description="dry: leave the rides the rain gauge says were wet, to answer")
    rules: dict[str, int | None] = Field(default_factory=dict, description="bike_by_kind: kind of ride (or commute, "
                                         "default): bike id; kept as the rider's Garmin rules too")


@router.post("/rides/bulk")
async def bulk_rides(request: Request, body: BulkIn) -> dict[str, Any]:
    """Answer many rides at once: every unanswered outdoor ride dry; every bike as it is; or every ride without a bike
    the bike its kind of ride (from Garmin, or its CSV) goes to."""
    engine = request.app.state.engine
    row = await _rider(engine, body.rider)
    q = {"r": body.rider}
    if body.action == "bike_by_kind":
        from app.bikes import garmin as G

        async with engine.connect() as c:
            mine = {r.id for r in await c.execute(text("SELECT id FROM bike WHERE rider_id = :r"), q)}
        if any(v is not None and v not in mine for v in body.rules.values()):
            raise HTTPException(422, "Not one of this rider's bikes")
        g = G._settings(row)
        g["rules"] = {**(g.get("rules") or {}), **{k: v for k, v in body.rules.items() if v is not None}}
        for k, v in body.rules.items():
            if v is None:
                g["rules"].pop(k, None)
        await G._save_settings(engine, body.rider, g)
        return {"changed": await G.assign_by_kind(engine, body.rider, g["rules"]), "rules": g["rules"]}
    async with engine.begin() as c:
        if body.action == "dry":
            sql = "UPDATE bike_ride SET wet = false WHERE rider_id = :r AND wet IS NULL AND NOT indoor"
            if body.keep_suggested_wet:
                sql += " AND wet_suggested IS NOT TRUE"
        else:
            sql = "UPDATE bike_ride SET bike_checked = true WHERE rider_id = :r AND bike_id IS NOT NULL AND NOT bike_checked"
        n = (await c.execute(text(sql), q)).rowcount
    return {"changed": n}


@router.delete("/rides/{ride_id:path}")
async def delete_ride(request: Request, ride_id: str) -> dict[str, Any]:
    """Delete a ride (logged twice, or not a ride at all): its wear comes off the parts. An imported ride deleted here
    comes back if the same file is imported again."""
    async with request.app.state.engine.begin() as c:
        if not (await c.execute(text("DELETE FROM bike_ride WHERE id = :i"), {"i": ride_id})).rowcount:
            raise HTTPException(404, "No such ride")
    return {"id": ride_id, "deleted": True}


def _number(s: str) -> float | None:
    s = (s or "").replace(",", "").strip()
    try:
        return float(s) if s and s != "--" else None
    except ValueError:
        return None


def _hours(s: str) -> float:
    """'01:23:45', '1:23:45.6' or '83:12' (minutes and seconds) as hours."""
    parts = [p for p in re.split(r"[:]", (s or "").strip()) if p]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return 0.0
    if len(nums) == 3:
        return nums[0] + nums[1] / 60 + nums[2] / 3600
    if len(nums) == 2:
        return nums[0] / 60 + nums[1] / 3600
    return 0.0


CYCLING = re.compile(r"cycl|bik|ride|gravel|mountain|virtual|indoor|e-?bike|road", re.IGNORECASE)
INDOOR = re.compile(r"indoor|virtual|trainer|zwift", re.IGNORECASE)


def read_csv(text_: str, rider: str, miles: bool = False) -> list[dict[str, Any]]:
    """Garmin Connect's activities export (Activities, then Export CSV): its cycling rides, with their date, distance,
    moving time (or time), type and title. Distance is in the units Garmin Connect is set to (km unless miles)."""
    rows = list(csv.DictReader(io.StringIO(text_.lstrip("﻿"))))
    out = []
    for row in rows:
        low = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        kind = low.get("activity type", "")
        if kind and not CYCLING.search(kind):
            continue
        when = low.get("date") or low.get("start time") or ""
        try:
            started = datetime.fromisoformat(when.replace("/", "-")).replace(tzinfo=TZ)
        except ValueError:
            continue
        dist = _number(low.get("distance", ""))
        if dist is None:
            continue
        km = dist * 1.609344 if miles else dist
        hours = _hours(low.get("moving time") or low.get("time") or low.get("elapsed time") or "")
        title = low.get("title") or None
        out.append({"id": f"csv:{rider}:{started.isoformat()}:{round(km, 2)}", "rider": rider, "bike_id": None,
                    "started": started, "km": round(km, 2), "hours": round(hours, 3),
                    "indoor": bool(INDOOR.search(kind) or INDOOR.search(title or "")), "wet": None,
                    "activity": kind or None, "title": title, "source": "csv"})  # fmt: skip
    return out


class ImportIn(BaseModel):
    rider: str
    csv: str = Field(max_length=5_000_000)
    bike_id: int | None = Field(None, description="the bike for rides that do not say (default: ask on the page)")
    miles: bool = False


@router.post("/rides/import")
async def import_rides(request: Request, body: ImportIn) -> dict[str, Any]:
    """Garmin Connect's activities CSV. Rides already imported are skipped; outdoor rides of the last four weeks get a
    wet suggestion from the rain gauges."""
    await _rider(request.app.state.engine, body.rider)
    rides = read_csv(body.csv, body.rider, body.miles)
    if not rides:
        raise HTTPException(422, "No cycling rides found in that file (Garmin Connect: Activities, then Export CSV)")
    for r in rides:
        r["bike_id"] = body.bike_id
    await _suggest_wet(request, [r for r in rides if _now() - r["started"] < timedelta(days=27)])
    added = await _save_rides(request, rides)
    return {"read": len(rides), "added": added, "already": len(rides) - added}


@router.get("/rides")
async def list_rides(request: Request, rider: str = Query(...), limit: int = Query(100, ge=1, le=2000)) -> dict[str, Any]:
    async with request.app.state.engine.connect() as c:
        rows = [_out(_row(r)) for r in await c.execute(text("SELECT * FROM bike_ride WHERE rider_id = :r ORDER BY started DESC LIMIT :n"),
                                                       {"r": rider, "n": limit})]  # fmt: skip
    return {"rides": rows}


# --- everything for a rider ------------------------------------------------------------------------------------------
async def load(engine: AsyncEngine, rider: str) -> dict[str, Any]:
    """Every row the garage needs for one rider."""
    q = {"r": rider}
    async with engine.connect() as c:
        out = {
            "bikes": [_row(r) for r in await c.execute(text("SELECT * FROM bike WHERE rider_id = :r ORDER BY created"), q)],
            "sets": [_row(r) for r in await c.execute(text("SELECT * FROM bike_set WHERE rider_id = :r ORDER BY created"), q)],
            "parts": [_row(r) for r in await c.execute(text("SELECT * FROM bike_part WHERE rider_id = :r ORDER BY id"), q)],
            "rides": [_row(r) for r in await c.execute(text("SELECT * FROM bike_ride WHERE rider_id = :r ORDER BY started"), q)],
            "services": [_row(r) for r in await c.execute(text("SELECT * FROM bike_service WHERE rider_id = :r ORDER BY at"), q)],
            "fits": [_row(r) for r in await c.execute(text(
                "SELECT f.* FROM bike_fit f JOIN bike_part p ON f.item = 'part' AND f.item_id = p.id WHERE p.rider_id = :r "
                "UNION ALL SELECT f.* FROM bike_fit f JOIN bike_set s ON f.item = 'set' AND f.item_id = s.id WHERE s.rider_id = :r"), q)],
        }
    for b in out["bikes"]:
        b["has_photo"] = b.pop("photo", None) is not None
    for p in out["parts"]:
        p["has_photo"] = p.pop("photo", None) is not None
    return out


def build(rows: dict[str, Any], rider: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The garage as the page shows it."""
    st = {**SETTINGS, **(rider.get("settings") or {})}
    pl = W.Placer(rows["fits"])
    services, rides = rows["services"], rows["rides"]
    soon = now + timedelta(seconds=1)
    bikes = {b["id"]: {**_out(b), "sets": [], "parts": [], "batteries": []} for b in rows["bikes"]}
    sets_ = {s["id"]: {**_out(s), "parts": [], "bike_id": pl.bike_of_set(s["id"], soon)} for s in rows["sets"]}
    shelf, kit, due = [], [], []
    spare_kinds: dict[str, list[dict[str, Any]]] = {}
    for p in rows["parts"]:
        if p["status"] == "shelf" and p["quantity"] > 0:
            spare_kinds.setdefault(p["kind"], []).append(p)
    battery_life: dict[int, float] = {}
    parts_by_id = {p["id"]: p for p in rows["parts"]}
    replaced_by = {s["new_part_id"]: s for s in services if s.get("new_part_id")}
    retired_ids = {b["id"] for b in rows["bikes"] if b.get("retired_at")}

    def history(p: dict[str, Any]) -> list[dict[str, Any]]:
        """What was done to this part and to the ones it replaced, newest first ("SRAM 40t → Hope 42t")."""
        out_, seen, cur = [], set(), p
        while cur and cur["id"] not in seen:
            seen.add(cur["id"])
            out_ += [{"at": s["at"].isoformat(), "action": s["action"], "note": s.get("note"), "cost": _plain(s.get("cost")),
                      "part": describe(cur) or name_of(cur)} for s in services if s.get("part_id") == cur["id"]]  # fmt: skip
            prev = replaced_by.get(cur["id"])
            cur = parts_by_id.get(prev["part_id"]) if prev else None
        return sorted(out_, key=lambda x: x["at"], reverse=True)[:30]

    for p in rows["parts"]:
        if p["status"] == "retired":
            continue
        since = W.since_reset(p, services)
        out = {**_out(p), "name": name_of(p), "describe": describe(p), "kind_name": D.KINDS.get(p["kind"], D.KINDS["other"]).name,
               "measure": D.KINDS.get(p["kind"], D.KINDS["other"]).measure}  # fmt: skip
        out["history"] = history(p)
        if p["status"] == "shelf":
            shelf.append(out)
            continue
        if p["kind"] == "axs_battery":
            health = W.battery_health(p, services, rides, pl)
            out["health"] = health
            if health.get("learned_hours"):
                battery_life[p["id"]] = health["learned_hours"]
                p = {**p, "life": {"hours": health["learned_hours"]}}
        use = W.use_of(p, rides, pl, since, float(st["wet_factor"]), bool(st["trainer_has_cassette"]))
        out["wear"] = W.state(p, use, since, now)
        here = pl.now("part", p["id"])
        out["bike_id"] = pl.bike_of_part(p["id"], soon)
        out["set_id"] = here["set_id"] if here else None
        if out["wear"]["state"] in ("due", "coming") and out["bike_id"] not in retired_ids:  # a retired bike's parts are not due
            spares = spare_kinds.get(p["kind"]) or []
            due.append({"part_id": p["id"], "name": out["name"], "kind": p["kind"], "state": out["wear"]["state"],
                        "share": out["wear"]["share"], "measures": out["wear"]["measures"], "bike_id": out["bike_id"],
                        "measure": out["measure"], "on_list": bool(p.get("to_list_at")),
                        "spare": {"id": spares[0]["id"], "name": name_of(spares[0]), "left": sum(s["quantity"] for s in spares)} if spares else None})  # fmt: skip
        if p["status"] == "kit":  # rider kit; a battery in a bike's slot also shows on that bike
            kit.append(out)
            if out["bike_id"] in bikes:
                bikes[out["bike_id"]]["batteries"].append(out)
        elif out["set_id"] in sets_:
            sets_[out["set_id"]]["parts"].append(out)
        elif out["bike_id"] in bikes:
            bikes[out["bike_id"]]["parts"].append(out)
    for s in sets_.values():
        if s["bike_id"] in bikes and not s.get("retired_at"):
            bikes[s["bike_id"]]["sets"].append(s)
    from app.bikes import art

    for b in bikes.values():
        b["art"] = art.bike(b.get("config") or {}, b["label"])
        b["pressures"] = pressures(b, rider)
        b["km"] = round(sum(r["km"] for r in rides if r["bike_id"] == b["id"]), 1)
    for s in sets_.values():  # each wheel's odometer: the rides of the bike it was on at the time
        s["km"] = round(sum(r["km"] for r in rides if r["bike_id"] is not None and pl.bike_of_set(s["id"], r["started"]) == r["bike_id"]), 1)
    loose = [s for s in sets_.values() if s["bike_id"] is None and not s.get("retired_at")]
    # things to replace first, then services and checks; within each, the most worn first
    due.sort(key=lambda d: (d["state"] != "due", not D.KINDS.get(d["kind"], D.KINDS["other"]).bought, -d["share"]))
    ask = []  # rides to confirm: was it wet, and was it really this bike (Garmin often keeps the last one); latest 50
    for r in rides:
        if (r["wet"] is None and not r["indoor"]) or (not r["bike_checked"] and r["bike_id"] is not None):
            doubt = bike_doubt(r, rows["bikes"])
            ask.append({**_out(r), "doubt": doubt[0] if doubt else None, "suggest_bike": doubt[1] if doubt else None,
                        "garmin_link": garmin_link(r)})  # fmt: skip
    unassigned = [_out(r) for r in rides if r["bike_id"] is None]
    from app.bikes.garmin import kind_of

    kinds: dict[str, int] = {}
    for r in unassigned:
        k, commute = kind_of(r.get("activity"))
        for key in (["commute"] if commute else []) + [k or "default"]:
            kinds[key] = kinds.get(key, 0) + 1
    labels = {b["id"]: b["label"] for b in rows["bikes"]}
    logged = {s["part_id"] for s in services if s.get("part_id")}

    def entry(s: dict[str, Any]) -> dict[str, Any]:
        p = parts_by_id.get(s.get("part_id")) or {}
        bid = s.get("bike_id") or (pl.bike_of_part(p["id"], s["at"]) if p else None)
        if bid is None and p and s["action"] == "replaced":  # off at the replacement: where it was just before
            bid = pl.bike_of_part(p["id"], s["at"] - timedelta(seconds=1))
        return {"id": s["id"], "at": s["at"].isoformat(), "action": s["action"], "note": s.get("note"), "cost": _plain(s.get("cost")),
                "part": (describe(p) or name_of(p)) if p else None, "part_name": name_of(p) if p else None, "bike_id": bid,
                "bike": labels.get(bid),
                "undo": not (s["action"] == "replaced" and s.get("new_part_id") in logged)}  # fmt: skip

    log = [entry(s) for s in services[-25:]][::-1]
    wheel_sets = [x for x in rows["sets"] if x.get("kind") == "wheel"]

    def wheels_on(r: dict[str, Any]) -> list[str]:  # the wheels a ride counted against: those on its bike then
        return [x["label"] for x in wheel_sets if r["bike_id"] is not None and pl.bike_of_set(x["id"], r["started"]) == r["bike_id"]]

    recent = [{**_out(r), "bike": labels.get(r["bike_id"]), "wheels": wheels_on(r)} for r in rides[-25:]][::-1]
    c = costs(rows, now)
    by_bike = {x["bike_id"]: x for x in c["bikes"]}
    retired = []
    for b in rows["bikes"]:
        if not b.get("retired_at"):
            continue
        hist = [entry(s) for s in services if s.get("bike_id") == b["id"]
                or (s.get("part_id") in parts_by_id and entry(s)["bike_id"] == b["id"])][::-1]  # fmt: skip
        bike_rides = [r for r in rides if r["bike_id"] == b["id"]]
        on = bikes[b["id"]]
        retired.append({**_out(b), "has_photo": b.get("has_photo"), "art": on["art"], "km": on["km"], "rides": len(bike_rides),
                        "first_ride": bike_rides[0]["started"].isoformat() if bike_rides else None,
                        "last_ride": bike_rides[-1]["started"].isoformat() if bike_rides else None,
                        "parts": on["parts"] + [p for s in on["sets"] for p in s["parts"]], "sets": [s["label"] for s in on["sets"]],
                        "cost": by_bike.get(b["id"]), "history": hist[:100]})  # fmt: skip
    return {"rider": _out(rider), "settings": st, "bikes": list(bikes.values()), "loose_sets": loose, "shelf": shelf,
            "kit": kit, "due": due, "ask_wet": ask[-50:], "unassigned_rides": unassigned[-100:],
            "counts": {"to_confirm": len(ask), "unanswered_wet": sum(1 for r in rides if r["wet"] is None and not r["indoor"]),
                       "suggested_wet": sum(1 for r in rides if r["wet"] is None and not r["indoor"] and r.get("wet_suggested")),
                       "bike_unchecked": sum(1 for r in rides if not r["bike_checked"] and r["bike_id"] is not None),
                       "without_bike": len(unassigned), "kinds_without_bike": kinds},
            "log": log, "recent_rides": recent, "retired": retired, "costs": c}  # fmt: skip


# What a ride's activity type says about the bike it was on (Garmin's types: "Mountain Biking", "Gravel Cycling",
# "Road Cycling", "E-Bike Mountain"...): the bike types that fit it, and whether it had a motor
FITS = (("e[- ]?bike|e[- ]?mountain", None, True), ("mountain|mtb|trail|enduro|downhill", {"hardtail", "full_suspension"}, None),
        ("gravel|cyclocross|cx", {"gravel", "cyclocross"}, None), ("road", {"road", "tt", "gravel", "cyclocross", "tandem"}, None))  # fmt: skip


def bike_doubt(ride: dict[str, Any], bikes: list[dict[str, Any]]) -> tuple[str, int | None] | None:
    """Why the bike a ride has looks wrong, and a better one of the rider's bikes if just one fits: an MTB ride on the
    road bike, an e-bike ride on a bike without a motor. None when nothing says it is wrong."""
    act = f"{ride.get('activity') or ''} {ride.get('title') or ''}".lower()
    bike = next((b for b in bikes if b["id"] == ride.get("bike_id")), None)
    if not act.strip() or not bike or ride.get("indoor"):
        return None
    cfg = bike.get("config") or {}
    for pattern, types, ebike in FITS:
        if not re.search(pattern, act):
            continue
        if (types is None or cfg.get("type") in types) and (ebike is None or bool(cfg.get("ebike")) == ebike):
            return None
        better = [b for b in bikes if not b.get("retired_at") and b["id"] != bike["id"]
                  and (types is None or (b.get("config") or {}).get("type") in types)
                  and (ebike is None or bool((b.get("config") or {}).get("ebike")) == ebike)]  # fmt: skip
        what = (ride.get("activity") or ride.get("title") or "this ride").strip()
        return f"{what} logged on {bike['label']}", better[0]["id"] if len(better) == 1 else None
    return None


def garmin_link(ride: dict[str, Any]) -> str | None:
    """The ride in Garmin Connect, to change its bike there too (Garmin's API cannot)."""
    rid = str(ride.get("id") or "")
    return f"https://connect.garmin.com/modern/activity/{rid.split(':', 1)[1]}" if rid.startswith("garmin:") else None


def pressures(bike: dict[str, Any], rider: dict[str, Any]) -> dict[str, Any]:
    """Each fitted wheel's tyre pressure: the starting one, or the one you set."""
    cfg = bike.get("config") or {}
    system = (rider.get("weight_kg") or 80.0) + (bike.get("weight_kg") or BIKE_KG) + float(cfg.get("stoker_kg") or 0)
    out = {}
    for s in bike["sets"]:
        tyre = next((p for p in s["parts"] if p["kind"] == "tyre"), None)
        if not tyre or s.get("position") not in ("front", "rear"):
            continue
        d = tyre.get("details") or {}
        width = float(d.get("width_mm") or cfg.get(f"{s['position']}_width") or 28)
        suggested = D.tyre_pressure(width, bool(d.get("tubeless")), cfg.get("type", "road"), system, s["position"],
                                    d.get("max_psi"))  # fmt: skip
        psi = float(d["psi"]) if d.get("psi") else suggested
        out[s["position"]] = {"wheel": s["label"], "psi": psi, "bar": D.bar(psi), "suggested": suggested,
                              "own": bool(d.get("psi")), "width_mm": width, "tubeless": bool(d.get("tubeless"))}  # fmt: skip
    return out


def costs(rows: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Running cost: parts' prices (when fitted; a spare counts when it is fitted, not bought) and logged costs, per
    bike by year, with the cost per km; and each kind of part's cost per 1,000 km from the ones that were used up."""
    pl = W.Placer(rows["fits"])
    rides = rows["rides"]
    per: dict[int, dict[int, float]] = {}
    for p in rows["parts"]:
        if p.get("price") is None or p["status"] == "shelf" or not p.get("fitted_at"):
            continue
        bid = pl.bike_of_part(p["id"], p["fitted_at"] + timedelta(seconds=1))
        if bid is not None:
            per.setdefault(bid, {}).setdefault(p["fitted_at"].year, 0.0)
            per[bid][p["fitted_at"].year] += float(p["price"])
    for s in rows["services"]:
        if s.get("cost") is None or s["action"] == "replaced":  # a replacement's price is the new part's
            continue
        bid = s.get("bike_id") or (pl.bike_of_part(s["part_id"], s["at"]) if s.get("part_id") else None)
        if bid is not None:
            per.setdefault(bid, {}).setdefault(s["at"].year, 0.0)
            per[bid][s["at"].year] += float(s["cost"])
    bikes = []
    for b in rows["bikes"]:
        years = per.get(b["id"], {})
        km_by_year: dict[int, float] = {}
        for r in rides:
            if r["bike_id"] == b["id"]:
                km_by_year[r["started"].year] = km_by_year.get(r["started"].year, 0.0) + r["km"]
        total, km = sum(years.values()), sum(km_by_year.values())
        bikes.append({"bike_id": b["id"], "label": b["label"],
                      "years": {y: {"cost": round(v, 2), "km": round(km_by_year.get(y, 0), 1),
                                    "per_km": round(v / km_by_year[y], 3) if km_by_year.get(y) else None} for y, v in sorted(years.items())},
                      "total": round(total, 2), "per_km": round(total / km, 3) if km else None,
                      "purchase": (b.get("records") or {}).get("price")})  # fmt: skip
    kinds: dict[str, list[float]] = {}
    for p in rows["parts"]:  # used up: retired after a life, with a price
        if p["status"] != "retired" or p.get("price") is None or not p.get("fitted_at") or not p.get("retired_at"):
            continue
        u = W.use_of({**p, "kind": p["kind"]}, rides, pl, p["fitted_at"])
        km = u.km + (p.get("start_km") or 0)
        if km > 50:
            kinds.setdefault(p["kind"], []).append(float(p["price"]) / km * 1000)
    per_kind = {k: {"name": D.KINDS.get(k, D.KINDS["other"]).name, "per_1000km": round(sum(v) / len(v), 2), "parts": len(v)}
                for k, v in kinds.items()}  # fmt: skip
    return {"bikes": bikes, "kinds": per_kind, "currency": "GBP", "at": now.isoformat()}


@router.get("")
async def garage(request: Request, rider: str = Query(...)) -> dict[str, Any]:
    """Everything for a rider: bikes with their wheels and parts (each with its wear), loose sets, the due list,
    batteries, the shelf, kit, rides to confirm as wet or not, tyre pressures and running cost."""
    r = await _rider(request.app.state.engine, rider)
    return build(await load(request.app.state.engine, rider), r, _now())


@router.get("/costs")
async def get_costs(request: Request, rider: str = Query(...)) -> dict[str, Any]:
    await _rider(request.app.state.engine, rider)
    return costs(await load(request.app.state.engine, rider), _now())


# --- the Outdoors list -----------------------------------------------------------------------------------------------
def list_name(p: dict[str, Any]) -> str:
    """What goes on the Outdoors list: the brand and model when known, else the kind."""
    bm = " ".join(x for x in (p.get("brand"), p.get("model")) if x)
    return bm or D.KINDS.get(p["kind"], D.KINDS["other"]).name


async def put_on_list(request: Request, part: dict[str, Any], bike_label: str | None) -> dict[str, Any]:
    from app import shopping as S
    from app.outdoors import the_list

    _, lid, lname = await the_list(request)
    why = f"for {bike_label}" if bike_label else "bike garage"
    got = await S.add_item(request, list_name(part), why, list_id=lid)
    async with request.app.state.engine.begin() as c:
        await c.execute(text("UPDATE bike_part SET to_list_at = now() WHERE id = :p"), {"p": part["id"]})
    return {"list": lname, **got}


@router.post("/parts/{part_id}/to-list")
async def to_list(request: Request, part_id: int) -> dict[str, Any]:
    """Put a part (its brand and model, or its kind) on the Outdoors list in KitchenOwl."""
    async with request.app.state.engine.connect() as c:
        row = (await c.execute(text("SELECT * FROM bike_part WHERE id = :p"), {"p": part_id})).first()
    if not row:
        raise HTTPException(404, "No such part")
    return await put_on_list(request, _row(row), None)


async def due_to_list(request: Any, engine: AsyncEngine) -> dict[str, Any]:
    """Each rider whose setting is "auto": due parts with no spare on the shelf go onto the Outdoors list, once each."""
    added, notes = [], []
    async with engine.connect() as c:
        riders_ = [_row(r) for r in await c.execute(text("SELECT * FROM bike_rider"))]
    for r in riders_:
        if ({**SETTINGS, **(r["settings"] or {})})["to_outdoors"] != "auto":
            continue
        rows = await load(engine, r["id"])
        g = build(rows, r, _now())
        labels = {b["id"]: b["label"] for b in g["bikes"]}
        by_id = {p["id"]: p for p in rows["parts"]}
        for d in g["due"]:
            if d["state"] != "due" or d["on_list"] or d["spare"] or not D.KINDS.get(d["kind"], D.KINDS["other"]).bought:
                continue
            part = by_id[d["part_id"]]
            try:
                await put_on_list(request, part, labels.get(d["bike_id"]))
                added.append(f"{r['name']}: {d['name']}")
            except HTTPException as exc:
                notes.append(str(exc.detail))
                break
    return {"added": added, "notes": notes[:3]}



async def bikes_check(ctx: dict[str, Any]) -> Any:
    """The worker's daily check: due parts onto the Outdoors list for riders who chose that."""
    from app.jobs import tracked
    from app.wishlist import _Req

    async def run() -> Any:
        return await due_to_list(_Req(ctx["engine"]), ctx["engine"])

    return await tracked(ctx["engine"], "bikes_check", run)


# --- a product page's details ----------------------------------------------------------------------------------------
async def public_https(url: str) -> None:
    """Refuse anything but a public https website: the lookup fetches what it is given, so it must not be pointed at
    this server, the home network, or anything else private (every address the name resolves to is checked)."""
    import asyncio
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise HTTPException(422, "Give the product page's https:// address")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(u.hostname, u.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise HTTPException(502, f"{u.hostname} could not be found") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise HTTPException(422, "That address is not a public website")


class LookupIn(BaseModel):
    url: str = Field(max_length=500, pattern=r"^https://")


@router.post("/lookup")
async def lookup(body: LookupIn) -> dict[str, Any]:
    """A product page's name, brand and picture, from the schema.org data or Open Graph tags it publishes, to fill in a
    part. The picture is linked, credited to the page, not copied."""
    from app.integrations.outdoors.clients import HEADERS, _ld_products

    url = body.url
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
            for _ in range(4):  # each address, the first and every redirect, checked before it is asked
                await public_https(url)
                r = await c.get(url, headers={**HEADERS, "Accept": "text/html"})
                if r.status_code not in (301, 302, 303, 307, 308) or not r.headers.get("location"):
                    break
                url = str(r.url.join(r.headers["location"]))
            else:
                raise HTTPException(502, "That page redirected too many times")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"That page could not be reached ({type(exc).__name__})") from exc
    if r.status_code >= 400:
        raise HTTPException(502, f"That page answered HTTP {r.status_code} (some shops refuse automated requests)")
    html = r.text
    found = _ld_products(html)
    p = found[0] if found else {}
    brand = p.get("brand")
    img = p.get("image")
    img = img[0] if isinstance(img, list) and img else img.get("url") if isinstance(img, dict) else img

    def og(prop: str) -> str | None:
        m = re.search(rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)', html, re.IGNORECASE)
        return m.group(1) if m else None

    return {"name": p.get("name") or og("title"), "brand": brand.get("name") if isinstance(brand, dict) else brand,
            "image": img or og("image"), "source": body.url}  # fmt: skip

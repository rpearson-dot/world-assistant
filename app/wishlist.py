"""The wishlist: a KitchenOwl list of things to look out for in Aldi's Specialbuys and the Middle of Lidl.

The wishes live in KitchenOwl (a list called "Wishlist" in the shopping household, or the one chosen here), so anyone
in the family adds one in the KitchenOwl app and ticks it off when it is bought. World Assistant searches Aldi and
Lidl for each and keeps every time something matched, so it can say when an item was last seen.

An item's description can narrow it: "under £300" (or "max £300", "£300") is a top price, and "must: kamado"
lists words a product's name has to have (otherwise every word of the item's name). Anything else is left alone.

    GET  /v1/wishlist                   every wish, what is coming or on sale now, and when it was last seen
    GET  /v1/wishlist/{key}/seen        every time one was seen, newest first (key: "ko:<item id>")
    POST /v1/wishlist/check             search both now ({"item": key} for one); also the worker, Wednesday and
                                        Saturday evening, before Aldi's Thursday and Sunday Specialbuys
    GET|PUT /v1/wishlist/settings       which KitchenOwl list is the wishlist
    POST /v1/wishlist/items             add a wish to that list; DELETE /v1/wishlist/items/{key} ticks one off

"Seen" is a product turning up in the retailer's own search with every word asked for. A past Specialbuy the retailer
still lists (sold out, with its old on-sale date) counts too, so "last seen" can go back before the wishlist did.
Nothing is ever bought or put in a basket.

    docker compose exec api python -m app.wishlist kamado bbq       what each retailer sends, to check it works
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from datetime import UTC, date, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import shopping as S
from app.auth import require_token
from app.integrations.wishlist import clients as C

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/wishlist", tags=["wishlist"], dependencies=[Depends(require_token)])
RETAILERS = ("aldi", "lidl")
LAST_KEY = "wishlist_last_check"
SETTINGS_KEY = "wishlist_settings"
DEFAULT_LIST = "Wishlist"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
LIVE = ("coming", "on sale")
PRICE = re.compile(r"(?:under|max(?:imum)?|up to|below|<)?\s*£\s*(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
MUST = re.compile(r"must(?: have)?\s*:\s*([^;\n]+)", re.IGNORECASE)


class SettingsIn(BaseModel):
    list_id: int | None = Field(None, description="the KitchenOwl list to use; none: the one called Wishlist")


class CheckIn(BaseModel):
    item: str | None = Field(None, description='one wish ("ko:<id>"); default all')


class WishIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="what you want, e.g. Kamado BBQ")
    description: str = Field(default="", max_length=120, description='e.g. "under £300" or "must: kamado"')


def rules(description: str) -> tuple[list[str], float | None]:
    """The words a product must have and the top price, from an item's description."""
    d = description or ""
    m = MUST.search(d)
    must = [w for w in re.split(r"[,\s]+", m.group(1)) if w.strip()] if m else []
    p = PRICE.search(MUST.sub("", d))
    return must, float(p.group(1)) if p else None


async def _setting(engine: AsyncEngine, key: str) -> Any:
    async with engine.connect() as conn:
        v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": key})).scalar()
    return json.loads(v) if isinstance(v, str) else v


async def _put(engine: AsyncEngine, key: str, value: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                           {"k": key, "v": json.dumps(value)})  # fmt: skip


async def find_list(request: Request, settings_key: str, default: str, page: str) -> tuple[int, int, str]:
    """(household id, list id, list name) of a KitchenOwl list World Assistant watches: the one chosen (saved under
    settings_key), or the shopping household's list called `default`."""
    st = await S.load_settings(request)
    if not st["household_id"]:
        raise HTTPException(409, f"Set up the Shopping page first: the {default} list is in the same KitchenOwl household")
    lists = await S.kitchenowl_get(f"/api/household/{st['household_id']}/shoppinglist")
    chosen = (await _setting(request.app.state.engine, settings_key) or {}).get("list_id")
    hit = next((x for x in lists if x.get("id") == chosen), None) if chosen else None
    hit = hit or next((x for x in lists if str(x.get("name", "")).strip().lower() == default.lower()), None)
    if hit is None:
        raise HTTPException(409, f'No KitchenOwl list called "{default}" yet: add one in the KitchenOwl app, '
                                 f"or choose another list on the {page} page")  # fmt: skip
    return int(st["household_id"]), int(hit["id"]), str(hit.get("name") or default)


async def the_list(request: Request) -> tuple[int, int, str]:
    """(household id, list id, list name) of the wishlist: the chosen list, or the household's list called Wishlist."""
    return await find_list(request, SETTINGS_KEY, DEFAULT_LIST, "Wishlist")


async def items(request: Request) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The wishlist's list and its items, each with the words and top price it asks for."""
    hh, lid, lname = await the_list(request)
    lists = await S.kitchenowl_get(f"/api/household/{hh}/shoppinglist")
    out = []
    for it in S.list_items(lists, lid):
        must, top = rules(it["description"])
        out.append({**it, "query": it["name"], "must": must, "max_price": top})
    return {"household_id": hh, "list_id": lid, "name": lname}, out


def _row(r: Any) -> dict[str, Any]:
    d = dict(r._mapping)
    if d.get("price") is not None:
        d["price"] = float(d["price"])
    for k in ("first_seen", "last_seen", "on_sale"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def sightings(engine: AsyncEngine, wish: dict[str, Any]) -> list[dict[str, Any]]:
    """Everything that matched this wish, newest first: by its KitchenOwl item, or its name if the item was made anew."""
    async with engine.connect() as conn:
        rows = [_row(r) for r in await conn.execute(text(
            "SELECT item_key, item_name, retailer, product_id, name, brand, price, url, on_sale, status, availability, "
            "first_seen, last_seen FROM wishlist_seen WHERE item_key = :k OR lower(item_name) = lower(:n) "
            "ORDER BY last_seen DESC, on_sale DESC NULLS LAST"), {"k": wish["key"], "n": wish["name"]})]  # fmt: skip
    today = C.uk_today()
    for s in rows:  # a coming date that has passed is on sale now (until a check says otherwise)
        if s["status"] == "coming" and s["on_sale"] and date.fromisoformat(s["on_sale"]) <= today:
            s["status"] = "on sale"
    return rows


def summary(wish: dict[str, Any], seen: list[dict[str, Any]]) -> dict[str, Any]:
    dated = [s for s in seen if s["on_sale"]]
    return {**wish, "now": [s for s in seen if s["status"] in LIVE], "last_seen": seen[0] if seen else None,
            "last_on_sale": max(dated, key=lambda s: s["on_sale"]) if dated else None, "times_seen": len(seen)}  # fmt: skip


async def wishes(request: Request) -> dict[str, Any]:
    """Every wish with what is coming or on sale now and the last time anything matched."""
    lst, its = await items(request)
    engine = request.app.state.engine
    out = [summary(w, await sightings(engine, w)) for w in its]
    return {"list": lst, "items": out, "last_check": await _setting(engine, LAST_KEY)}


async def check(engine: AsyncEngine, client: httpx.AsyncClient, wishes_: list[dict[str, Any]],
                today: date | None = None, record_last: bool = True) -> dict[str, Any]:  # fmt: skip
    """Search both retailers for each wish and record what matched. One retailer failing does not stop the other."""
    today = today or C.uk_today()
    errors: dict[str, str] = {}
    found: dict[str, list[dict[str, Any]]] = {}
    for w in wishes_:
        for r in RETAILERS:
            if r in errors:
                continue
            try:
                got = await C.search(client, r, w["query"], today)
            except C.ShopError as exc:
                errors[r] = str(exc)
                continue
            for p in got:
                if C.matches(p["name"], w["query"], w["must"]) and (
                        not w["max_price"] or p["price"] is None or p["price"] <= w["max_price"]):  # fmt: skip
                    found.setdefault(w["key"], []).append(p)
            await asyncio.sleep(1)  # gently
    names = {w["key"]: w["name"] for w in wishes_}
    async with engine.begin() as conn:
        for key, ps in found.items():
            for p in ps:
                await conn.execute(text(
                    "INSERT INTO wishlist_seen (item_key, item_name, retailer, product_id, name, brand, price, url, "
                    "on_sale, status, availability) VALUES (:k, :iname, :r, :pid, :n, :b, :pr, :u, :d, :s, :a) "
                    "ON CONFLICT (item_key, retailer, product_id) DO UPDATE SET item_name = EXCLUDED.item_name, "
                    "name = EXCLUDED.name, brand = EXCLUDED.brand, price = COALESCE(EXCLUDED.price, wishlist_seen.price), "
                    "url = COALESCE(EXCLUDED.url, wishlist_seen.url), on_sale = COALESCE(EXCLUDED.on_sale, wishlist_seen.on_sale), "
                    "status = EXCLUDED.status, availability = EXCLUDED.availability, last_seen = now()"),
                    {"k": key, "iname": names[key], "r": p["retailer"], "pid": p["id"], "n": p["name"], "b": p["brand"],
                     "pr": p["price"], "u": p["url"], "d": date.fromisoformat(p["on_sale"]) if p["on_sale"] else None,
                     "s": p["status"], "a": p["availability"]})  # fmt: skip
    last = {"at": datetime.now(UTC).isoformat(), "wishes": len(wishes_), "matched": sum(len(v) for v in found.values()),
            "errors": errors or None}  # fmt: skip
    if record_last:
        await _put(engine, LAST_KEY, last)
    return {**last, "found": found}


@router.get("")
async def list_wishes(request: Request) -> dict[str, Any]:
    """Every wish on the KitchenOwl wishlist: `now` is what is coming or on sale, `last_seen` the latest match,
    `last_on_sale` the latest on-sale date any match had."""
    return await wishes(request)


@router.get("/settings")
async def get_wishlist_settings(request: Request) -> dict[str, Any]:
    """Which KitchenOwl list is the wishlist, and the household's lists to choose from."""
    st = await S.load_settings(request)
    chosen = (await _setting(request.app.state.engine, SETTINGS_KEY) or {}).get("list_id")
    out: dict[str, Any] = {"list_id": chosen, "default_name": DEFAULT_LIST, "lists": []}
    if st["household_id"]:
        lists = await S.kitchenowl_get(f"/api/household/{st['household_id']}/shoppinglist")
        out["lists"] = [{"id": x["id"], "name": x.get("name", ""), "items": len(x.get("items") or [])} for x in lists]
    try:
        out["using"] = dict(zip(("household_id", "list_id", "name"), await the_list(request), strict=True))
    except HTTPException as exc:
        out["problem"] = exc.detail
    return out


@router.put("/settings")
async def put_wishlist_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """Choose the KitchenOwl list to use as the wishlist (none: the one called Wishlist). Not the shopping list."""
    st = await S.load_settings(request)
    if body.list_id is not None and body.list_id == st["list_id"]:
        raise HTTPException(422, "that is the shopping list: make a separate list for the wishlist")
    await _put(request.app.state.engine, SETTINGS_KEY, {"list_id": body.list_id})
    return await get_wishlist_settings(request)


@router.post("/items", status_code=201)
async def add_wish(request: Request, body: WishIn) -> dict[str, Any]:
    """Put a wish on the KitchenOwl wishlist, as adding it in the app does, and check Aldi and Lidl for it."""
    _, lid, _ = await the_list(request)
    got = await S.add_item(request, body.name, body.description, list_id=lid)
    return {**got, **await check_now(request, CheckIn(item=got["item"]["key"]))}


@router.delete("/items/{key}", status_code=204)
async def remove_wish(request: Request, key: str) -> None:
    """Tick a wish off the KitchenOwl wishlist (its history stays, for if it comes back)."""
    _, lid, _ = await the_list(request)
    if not await S.remove_items(request, [key], list_id=lid):
        raise HTTPException(422, 'say which item, as "ko:<id>"')


@router.get("/{key}/seen")
async def seen(request: Request, key: str) -> dict[str, Any]:
    """Every product that matched this wish, the latest first."""
    _, its = await items(request)
    w = next((i for i in its if i["key"] == key), None)
    if w is None:
        raise HTTPException(404, f"no wish {key} on the wishlist")
    return {"item": w, "seen": await sightings(request.app.state.engine, w)}


@router.post("/check")
async def check_now(request: Request, body: CheckIn | None = None) -> dict[str, Any]:
    """Search Aldi and Lidl now, for every wish or one. Takes a second or two per wish."""
    _, its = await items(request)
    one = body.item if body else None
    todo = [i for i in its if one is None or i["key"] == one]
    if one is not None and not todo:
        raise HTTPException(404, f"no wish {one} on the wishlist")
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        out = await check(request.app.state.engine, c, todo, record_last=one is None)
    return {k: v for k, v in out.items() if k != "found"} | await wishes(request)


class _Req:
    """Just enough of a request for the worker to read the settings and KitchenOwl as the API does."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.app = type("A", (), {"state": type("S", (), {"engine": engine})()})()


async def wishlist_check(ctx: dict[str, Any]) -> Any:
    """The worker's check, Wednesday and Saturday evening."""
    from app.jobs import tracked

    async def run() -> Any:
        req: Any = _Req(ctx["engine"])
        try:
            _, its = await items(req)
        except HTTPException as exc:
            return f"skipped: {exc.detail}"
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            out = await check(ctx["engine"], c, its)
        log.info("wishlist: %s wishes, %s matches, errors %s", out["wishes"], out["matched"], out["errors"])
        return {k: v for k, v in out.items() if k != "found"}

    return await tracked(ctx["engine"], "wishlist_check", run)


async def probe(query: str) -> None:
    """Print what each retailer sends for a search, raw and as read here."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for r in RETAILERS:
            print(f"\n== {C.NAMES[r]}: {query!r}")
            try:
                got = await C.search(c, r, query)
                print(f"{len(got)} products read; {sum(C.matches(p['name'], query) for p in got)} have every word")
                print(json.dumps(got[:6], indent=2, default=str))
                raw = await C.search(c, r, query, raw=True)
                print(f"-- raw ({type(raw).__name__}), first 2500 characters:")
                print(json.dumps(raw, indent=1, default=str)[:2500])
            except C.ShopError as exc:
                print(f"FAILED: {exc}")


def main() -> int:
    asyncio.run(probe(" ".join(sys.argv[1:]) or "kamado bbq"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Outdoors: a KitchenOwl list of outdoor gear, priced every day at GO Outdoors and Decathlon, with price drops.

The things live in KitchenOwl (a list called "Outdoors" in the shopping household, or the one chosen here), so anyone
in the family adds one in the KitchenOwl app and ticks it off when it is bought. An item's description says what to
follow:

    a GO Outdoors or Decathlon product link    that product exactly (Decathlon is only ever read this way)
    nothing                                     GO Outdoors' search and Bikesy's deals (UK bike shops) for the item's
                                                name: the three cheapest matches at each
    "under £300", "must: gore-tex"             a top price, and words a product's name has to have

    GET  /v1/outdoors                    every item with the products it follows: price now, before, lowest, and drops
    GET  /v1/outdoors/{key}/history      every price each product had
    POST /v1/outdoors/check              check now ({"item": key} for one); also the worker, every morning
    GET|PUT /v1/outdoors/settings        which KitchenOwl list is the Outdoors list
    POST /v1/outdoors/items              add an item to that list; DELETE /v1/outdoors/items/{key} ticks one off

A price drop is a product found cheaper than at its last check; it counts as a drop for seven days (Home Assistant's
sensor and the page say so). Nothing is ever bought.

    docker compose exec api python -m app.outdoors <words, or a product link>      what each shop sends
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import shopping as S
from app.auth import require_token
from app.config import get_settings
from app.integrations.outdoors import clients as O
from app.integrations.wishlist import clients as C
from app.wishlist import _put, _setting, find_list, rules

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/outdoors", tags=["outdoors"], dependencies=[Depends(require_token)])
SETTINGS_KEY = "outdoors_settings"
LAST_KEY = "outdoors_last_check"
DEFAULT_LIST = "Outdoors"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
DROP_DAYS = 7
SEARCH_KEEP = 3


class SettingsIn(BaseModel):
    list_id: int | None = Field(None, description="the KitchenOwl list to use; none: the one called Outdoors")


class CheckIn(BaseModel):
    item: str | None = Field(None, description='one item ("ko:<id>"); default all')


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="what you want, e.g. Two-man tent")
    description: str = Field(default="", max_length=120, description="a product link, or e.g. under £300")


async def the_list(request: Request) -> tuple[int, int, str]:
    return await find_list(request, SETTINGS_KEY, DEFAULT_LIST, "Outdoors")


async def items(request: Request) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    hh, lid, lname = await the_list(request)
    lists = await S.kitchenowl_get(f"/api/household/{hh}/shoppinglist")
    out = []
    for it in S.list_items(lists, lid):
        rest = O.URL.sub("", it["description"])
        must, top = rules(rest)
        out.append({**it, "links": O.links(it["description"]), "must": must, "max_price": top})
    return {"household_id": hh, "list_id": lid, "name": lname}, out


def _row(r: Any) -> dict[str, Any]:
    d = dict(r._mapping)
    for k in ("price", "previous_price", "was_price", "lowest_price"):
        if d.get(k) is not None:
            d[k] = float(d[k])
    for k in ("lowest_at", "dropped_at", "first_seen", "last_seen", "at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def products(engine: AsyncEngine, key: str) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=DROP_DAYS)
    async with engine.connect() as conn:
        rows = [_row(r) for r in await conn.execute(text(
            "SELECT retailer, product_id, name, brand, url, pinned, price, previous_price, was_price, lowest_price, "
            "lowest_at, available, dropped_at, error, error_at, currency, first_seen, last_seen FROM outdoors_product WHERE item_key = :k "
            "ORDER BY pinned DESC, price NULLS LAST"), {"k": key})]  # fmt: skip
    for p in rows:
        p["dropped"] = bool(p["dropped_at"] and datetime.fromisoformat(p["dropped_at"]) >= since)
    return rows


async def overview(request: Request) -> dict[str, Any]:
    """Every item with the products it follows."""
    lst, its = await items(request)
    engine = request.app.state.engine
    out = []
    for it in its:
        ps = await products(engine, it["key"])
        pounds = [p for p in ps if p["price"] is not None and p.get("currency", "GBP") == "GBP"]  # like with like
        out.append({**it, "products": ps, "cheapest": min(p["price"] for p in pounds) if pounds else None,
                    "dropped": [p for p in ps if p["dropped"]],
                    "under": [p for p in pounds if it["max_price"] and p["price"] <= it["max_price"]],
                    "failing": [p for p in ps if p.get("error")]})  # fmt: skip
    return {"list": lst, "items": out, "last_check": await _setting(engine, LAST_KEY)}


def searches() -> list[tuple[str, Any]]:
    """(source, search) for an item without a link: GO Outdoors, Bikesy, and each Tradeinn shop chosen."""
    from functools import partial

    shops = [x.strip().lower() for x in get_settings().outdoors_tradeinn_shops.split(",") if x.strip()]
    return [("gooutdoors", O.go_search), ("bikesy", O.bikesy_search)] + [
        (f"tradeinn:{s}", partial(O.tradeinn_search, shop=s)) for s in shops if s in O.TRADEINN_SHOPS]


async def read(client: httpx.AsyncClient, it: dict[str, Any], down: dict[str, str] | None = None,
               ) -> tuple[list[dict[str, Any]], list[str], list[tuple[str, str]]]:  # fmt: skip
    """The products an item follows as the shops have them now, what could not be read, and the links that failed.
    A source that fails is put in `down` (source -> why) and not asked again in the same check, so one shop that is
    down or blocking costs one failed request, not one per item."""
    down = {} if down is None else down
    got: list[dict[str, Any]] = []
    errors: list[str] = []
    failed_links: list[tuple[str, str]] = []
    for retailer, url in it["links"]:
        if retailer in down:
            failed_links.append((url, down[retailer]))
            continue
        try:
            p = await (O.go_product(client, url) if retailer == "gooutdoors" else O.decathlon_product(client, url))
            got.append({**p, "pinned": True})
        except C.ShopError as exc:
            errors.append(str(exc))
            failed_links.append((url, str(exc)))
            if "no such page" not in str(exc):  # a dead link is that product's problem, not the shop's
                down[retailer] = str(exc)
        await asyncio.sleep(1)
    if not it["links"]:
        for source, search in searches():
            if source in down:
                continue
            try:
                found = [p for p in await search(client, it["name"]) if C.matches(p["name"], it["name"], it["must"])]
                found.sort(key=lambda p: (p["price"] is None, p["price"] or 0))
                got += [{**p, "pinned": False} for p in found[:SEARCH_KEEP]]
            except C.ShopError as exc:
                errors.append(str(exc))
                down[source] = str(exc)
            except Exception as exc:  # noqa: BLE001 - an answer of a shape nobody expected: that shop, this time
                log.warning("outdoors: %s failed for %r: %s", source, it["name"], exc)
                errors.append(f"{source}: could not read its answer ({type(exc).__name__})")
                down[source] = f"could not read its answer ({type(exc).__name__})"
            await asyncio.sleep(1)
    return got, errors, failed_links


async def mark_failed(engine: AsyncEngine, it: dict[str, Any], failed: list[tuple[str, str]], now: datetime) -> None:
    """A followed product that could not be read keeps its last price, and says why and since when."""
    if not failed:
        return
    async with engine.begin() as conn:
        for url, why in failed:
            await conn.execute(text("UPDATE outdoors_product SET error = :e, error_at = COALESCE(error_at, :now) "
                                    "WHERE item_key = :k AND url = :u"), {"e": why[:300], "now": now, "k": it["key"], "u": url})


async def record(engine: AsyncEngine, it: dict[str, Any], got: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Store what was read; returns the products whose price fell."""
    drops = []
    async with engine.begin() as conn:
        for p in got:
            old = (await conn.execute(text(
                "SELECT price, lowest_price FROM outdoors_product WHERE item_key = :k AND retailer = :r AND product_id = :p"),
                {"k": it["key"], "r": p["retailer"], "p": p["id"]})).first()  # fmt: skip
            price = p["price"]
            was_price = float(old.price) if old and old.price is not None else None
            fell = price is not None and was_price is not None and price < was_price
            changed = price is not None and price != was_price
            lows = [x for x in (price, float(old.lowest_price) if old and old.lowest_price is not None else None) if x is not None]
            low = min(lows) if lows else None
            await conn.execute(text(
                "INSERT INTO outdoors_product (item_key, retailer, product_id, item_name, name, brand, url, pinned, price, "
                "previous_price, was_price, lowest_price, lowest_at, available, dropped_at, last_seen, currency) VALUES "
                "(:k, :r, :p, :iname, :n, :b, :u, :pin, CAST(:price AS numeric), NULL, CAST(:was AS numeric), "
                "CAST(:price AS numeric), CASE WHEN CAST(:price AS numeric) IS NULL THEN NULL ELSE CAST(:now AS timestamptz) END, "
                ":avail, NULL, :now, :cur) ON CONFLICT (item_key, retailer, product_id) DO UPDATE SET item_name = EXCLUDED.item_name, "
                "name = EXCLUDED.name, brand = EXCLUDED.brand, url = EXCLUDED.url, pinned = EXCLUDED.pinned, "
                "previous_price = CASE WHEN CAST(:changed AS boolean) THEN outdoors_product.price ELSE outdoors_product.previous_price END, "
                "price = COALESCE(EXCLUDED.price, outdoors_product.price), was_price = EXCLUDED.was_price, "
                "lowest_price = CAST(:low AS numeric), lowest_at = CASE WHEN CAST(:low AS numeric) IS DISTINCT FROM "
                "outdoors_product.lowest_price THEN CAST(:now AS timestamptz) ELSE outdoors_product.lowest_at END, "
                "available = EXCLUDED.available, dropped_at = CASE WHEN CAST(:fell AS boolean) THEN CAST(:now AS timestamptz) "
                "ELSE outdoors_product.dropped_at END, error = NULL, error_at = NULL, last_seen = :now, currency = EXCLUDED.currency"),
                {"k": it["key"], "r": p["retailer"], "p": p["id"], "iname": it["name"], "n": p["name"], "b": p["brand"],
                 "u": p["url"], "pin": p["pinned"], "price": price, "was": p["was"], "avail": p["available"],
                 "changed": changed, "low": low, "fell": fell, "now": now, "cur": p.get("currency") or "GBP"})  # fmt: skip
            if changed:
                await conn.execute(text("INSERT INTO outdoors_price (item_key, retailer, product_id, price, at) "
                                        "VALUES (:k, :r, :p, :price, :now)"),
                                   {"k": it["key"], "r": p["retailer"], "p": p["id"], "price": price, "now": now})  # fmt: skip
            if fell:
                drops.append({**p, "item": it["name"], "from": was_price})
    return drops


async def check(engine: AsyncEngine, client: httpx.AsyncClient, todo: list[dict[str, Any]],
                record_last: bool = True) -> dict[str, Any]:  # fmt: skip
    now = datetime.now(UTC)
    drops: list[dict[str, Any]] = []
    errors: dict[str, list[str]] = {}
    down: dict[str, str] = {}  # a shop that failed this time: not asked again until the next check
    for it in todo:
        got, errs, failed = await read(client, it, down)
        drops += await record(engine, it, got, now)
        await mark_failed(engine, it, failed, now)
        if errs:
            errors[it["name"]] = errs
    searched = any(not it["links"] for it in todo)
    used = sorted(({s for s, _ in searches()} if searched else set()) | {r for it in todo for r, _ in it["links"]})
    sources = {s: ("failed: " + down[s]) if s in down else "ok" for s in used}
    last = {"at": now.isoformat(), "items": len(todo), "drops": len(drops), "errors": errors or None, "sources": sources}
    if record_last:
        await _put(engine, LAST_KEY, last)
    return {**last, "fell": drops}


@router.get("")
async def list_outdoors(request: Request) -> dict[str, Any]:
    """Every item on the KitchenOwl Outdoors list with the products it follows: price now and before, the lowest
    seen, the shop's "was" price, stock, and whether it has dropped in the last seven days."""
    return await overview(request)


@router.get("/settings")
async def get_outdoors_settings(request: Request) -> dict[str, Any]:
    """Which KitchenOwl list is the Outdoors list, and the household's lists to choose from."""
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
async def put_outdoors_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """Choose the KitchenOwl list to use (none: the one called Outdoors). Not the shopping list."""
    st = await S.load_settings(request)
    if body.list_id is not None and body.list_id == st["list_id"]:
        raise HTTPException(422, "that is the shopping list: make a separate list for outdoor things")
    await _put(request.app.state.engine, SETTINGS_KEY, {"list_id": body.list_id})
    return await get_outdoors_settings(request)


@router.post("/items", status_code=201)
async def add_outdoors(request: Request, body: ItemIn) -> dict[str, Any]:
    """Put an item on the KitchenOwl Outdoors list and price it now."""
    _, lid, _ = await the_list(request)
    got = await S.add_item(request, body.name, body.description, list_id=lid)
    return {**got, **await check_now(request, CheckIn(item=got["item"]["key"]))}


@router.delete("/items/{key}", status_code=204)
async def remove_outdoors(request: Request, key: str) -> None:
    """Tick an item off the KitchenOwl Outdoors list (its prices stay, for if it comes back)."""
    _, lid, _ = await the_list(request)
    if not await S.remove_items(request, [key], list_id=lid):
        raise HTTPException(422, 'say which item, as "ko:<id>"')


@router.get("/{key}/history")
async def history(request: Request, key: str) -> dict[str, Any]:
    """Every price each product this item follows has had, newest first."""
    async with request.app.state.engine.connect() as conn:
        rows = [_row(r) for r in await conn.execute(text(
            "SELECT h.retailer, h.product_id, p.name, h.price, h.at FROM outdoors_price h LEFT JOIN outdoors_product p "
            "USING (item_key, retailer, product_id) WHERE h.item_key = :k ORDER BY h.at DESC LIMIT 200"), {"k": key})]  # fmt: skip
    return {"item": key, "prices": rows}


@router.post("/check")
async def check_now(request: Request, body: CheckIn | None = None) -> dict[str, Any]:
    """Price every item now (or one). Takes a second or two per item."""
    _, its = await items(request)
    one = body.item if body else None
    todo = [i for i in its if one is None or i["key"] == one]
    if one is not None and not todo:
        raise HTTPException(404, f"no item {one} on the Outdoors list")
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        out = await check(request.app.state.engine, c, todo, record_last=one is None)
    return out | await overview(request)


async def outdoors_check(ctx: dict[str, Any]) -> Any:
    """The worker's check, every morning."""
    from app.jobs import tracked
    from app.wishlist import _Req

    async def run() -> Any:
        try:
            _, its = await items(_Req(ctx["engine"]))  # type: ignore[arg-type]
        except HTTPException as exc:
            return f"skipped: {exc.detail}"
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            out = await check(ctx["engine"], c, its)
        log.info("outdoors: %s items, %s drops, errors %s", out["items"], out["drops"], out["errors"])
        return {k: v for k, v in out.items() if k != "fell"}

    return await tracked(ctx["engine"], "outdoors_check", run)


async def probe(arg: str) -> None:
    """Print what each shop sends, raw and as read here."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for retailer, url in O.links(arg) or [("gooutdoors", "")]:
            print(f"\n== {O.NAMES[retailer]}: {url or arg!r}")
            try:
                if url:
                    fn = O.go_product if retailer == "gooutdoors" else O.decathlon_product
                    print(json.dumps(await fn(c, url), indent=2, default=str))
                    print("-- raw, first 2500 characters:")
                    print(json.dumps(await fn(c, url, raw=True), indent=1, default=str)[:2500])
                else:
                    from functools import partial

                    tradeinn = [(f"Tradeinn ({x})", partial(O.tradeinn_search, shop=x)) for x in ("trekkinn", "bikeinn")]
                    for name, fn in (("GO Outdoors", O.go_search), ("Bikesy", O.bikesy_search), *tradeinn):
                        print(f"\n-- {name}")
                        try:
                            got = await fn(c, arg)
                            print(f"{len(got)} products; {sum(C.matches(p['name'], arg) for p in got)} have every word")
                            print(json.dumps(got[:6], indent=2, default=str))
                            print("-- raw, first 2500 characters:")
                            print(json.dumps(await fn(c, arg, raw=True), indent=1, default=str)[:2500])
                        except C.ShopError as exc:
                            print(f"FAILED: {exc}")
            except C.ShopError as exc:
                print(f"FAILED: {exc}")


def main() -> int:
    asyncio.run(probe(" ".join(sys.argv[1:]) or "two man tent"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

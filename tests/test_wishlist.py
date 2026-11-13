"""The wishlist: reading Aldi's and Lidl's searches, what counts as a match, and the KitchenOwl list it works from."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.modules.shopping import shopping as S
from app.modules.wishlist import wishlist as W
from app.modules.wishlist.sources.wishlist import clients as C

TODAY = date(2026, 11, 7)

ALDI = {"meta": {"totalCount": 3}, "data": [
    {"sku": "000000000412345001", "name": "Gardenline Kamado Ceramic Charcoal Grill", "brandName": "GARDENLINE",
     "urlSlugText": "gardenline-kamado-ceramic-charcoal-grill",
     "price": {"amount": 29999, "amountRelevantDisplay": "£299.99"}, "onSaleDateDisplay": "Thu 12 Nov",
     "badges": [{"text": "Specialbuy"}]},
    {"sku": "000000000499999001", "name": "Gardenline Kamado Grill Cover", "brandName": "GARDENLINE",
     "price": {"amount": 1499}, "notForSale": True, "badges": [{"text": "Sold out"}]},
    {"sku": "000000000488888001", "name": "Gardenline Kettle BBQ", "price": {"amount": 3999}},
]}  # fmt: skip
LIDL = {"numFound": 2, "items": [
    {"gridbox": {"data": {"productId": 100361234, "fullTitle": "Grillmeister Kamado Barbecue",
                          "canonicalUrl": "/p/grillmeister-kamado-barbecue/p100361234",
                          "price": {"price": 249.0, "oldPrice": 299.0},
                          "stockAvailability": {"availabilityIndicator": "In store from 05/11"},
                          "ribbons": [{"text": "Middle of Lidl"}]}}},
    {"gridbox": {"data": {"productId": 100370000, "fullTitle": "Parkside Pressure Washer", "price": {"price": 89.99}}}},
]}  # fmt: skip


def shops(seen: list[str], aldi: Any = ALDI, lidl: Any = LIDL, status: int = 200) -> httpx.MockTransport:
    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if status != 200:
            return httpx.Response(status, text="no")
        return httpx.Response(200, json=aldi if req.url.host == "api.aldi.co.uk" else lidl)

    return httpx.MockTransport(answer)


def test_dates_in_what_a_retailer_says() -> None:
    assert C.when("Thu 12 Nov", TODAY) == date(2026, 11, 12)
    assert C.when("In store from 05/11", TODAY) == date(2026, 11, 5)
    assert C.when("on sale 2026-05-14", TODAY) == date(2026, 5, 14)
    assert C.when("Sun 3rd Jan", TODAY) == date(2027, 1, 3)  # the nearer year
    assert C.when("Thu 14 May", TODAY) == date(2026, 5, 14)
    assert C.when("Specialbuy", TODAY) is None and C.when(None, TODAY) is None


def test_what_counts_as_the_thing_you_want() -> None:
    assert C.matches("Gardenline Kamado Ceramic Charcoal Grill", "Kamado BBQ")  # bbq ~ grill
    assert C.matches("Grillmeister Kamado Barbecue", "kamado bbq")
    assert not C.matches("Gardenline Kettle BBQ", "Kamado BBQ")
    assert C.matches("Gardenline Kettle BBQ", "Kamado BBQ", must=["bbq"])  # must replaces the name's words
    assert not C.matches("Crivit Ski Gloves", "ski jacket")
    assert C.matches("Crivit Ladies' Ski Jacket", "ski jacket")
    assert not C.matches("Gardenline Kamado Grill Cover", "Kamado BBQ")  # an accessory for it, not it
    assert C.matches("Gardenline Kamado Grill Cover", "kamado cover")  # unless that is what you want


async def test_aldi_and_lidl_are_read_whatever_their_shape() -> None:
    seen: list[str] = []
    async with httpx.AsyncClient(transport=shops(seen)) as c:
        aldi = await C.search(c, "aldi", "kamado bbq", TODAY)
        lidl = await C.search(c, "lidl", "kamado bbq", TODAY)
    a = parse_qs(urlparse(seen[0]).query)
    assert a["q"] == ["kamado"] and a["serviceType"] == ["walk-in"]
    assert parse_qs(urlparse(seen[1]).query)["assortment"] == ["GB"]
    k = aldi[0]
    assert (k["name"], k["price"], k["on_sale"], k["status"], k["brand"]) == (
        "Gardenline Kamado Ceramic Charcoal Grill", 299.99, "2026-11-12", "coming", "GARDENLINE")
    assert k["url"] == "https://www.aldi.co.uk/product/gardenline-kamado-ceramic-charcoal-grill-000000000412345001"
    assert aldi[1]["status"] == "sold out" and aldi[1]["price"] == 14.99
    assert aldi[2]["status"] == "listed"
    g = lidl[0]
    assert (g["id"], g["price"], g["on_sale"], g["status"]) == ("100361234", 249.0, "2026-11-05", "on sale")
    assert g["url"] == "https://www.lidl.co.uk/p/grillmeister-kamado-barbecue/p100361234"


async def test_a_refusal_says_so_in_words() -> None:
    async with httpx.AsyncClient(transport=shops([], status=403)) as c:
        with pytest.raises(C.ShopError, match="refused the search .*blocking automated"):
            await C.search(c, "aldi", "kamado", TODAY)
    t = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(C.ShopError, match="did not send JSON"):
            await C.search(c, "lidl", "kamado", TODAY)


def test_an_items_description_narrows_it() -> None:
    assert W.rules("") == ([], None)
    assert W.rules("under £300") == ([], 300.0)
    assert W.rules("must: kamado, ceramic; max £250.50") == (["kamado", "ceramic"], 250.5)
    assert W.rules("for the patio") == ([], None)


def test_the_page_script_compiles(tmp_path: Any) -> None:
    from app.modules.wishlist.wishlist_page import PAGE

    js = re.findall(r"<script>(.*?)</script>", PAGE, re.DOTALL)[-1]
    f = tmp_path / "wl.js"
    f.write_text(js)
    subprocess.run(["node", "--check", str(f)], check=True)


def test_the_page_calls_real_routes() -> None:
    from app.main import app
    from app.modules.wishlist.wishlist_page import PAGE

    paths = set(app.openapi()["paths"])
    for p in set(re.findall(r"'(/v1/wishlist[^']*)'", PAGE)):
        p = p.rstrip("/")
        assert any(x == p or x.startswith(p + "/") for x in paths), p


DB = os.environ.get("WA_TEST_DATABASE_URL")
LISTS = [{"id": 1, "name": "Shopping", "items": [{"id": 5, "name": "Milk"}]},
         {"id": 2, "name": "Wishlist", "items": [{"id": 77, "name": "Kamado BBQ", "description": "under £280"},
                                                 {"id": 78, "name": "Ski jacket"}]}]  # fmt: skip


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_the_kitchenowl_wishlist_is_checked_and_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy as sa
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.main import app

    sync = sa.create_engine(DB)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS wishlist_seen"))
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null,"
                          " updated_at timestamptz not null default now())"))  # fmt: skip
        conn.execute(text("""CREATE TABLE wishlist_seen (item_key text, retailer text, product_id text, item_name text not null,
            name text not null, brand text, price numeric(10,2), url text, on_sale date, status text not null, availability text,
            first_seen timestamptz not null default now(), last_seen timestamptz not null default now(),
            PRIMARY KEY (item_key, retailer, product_id))"""))
        conn.execute(text("INSERT INTO app_setting (key, value) VALUES (:k, CAST(:v AS jsonb))"),
                     {"k": S.SETTINGS_KEY, "v": json.dumps({"household_id": 9, "list_id": 1})})
    added: list[Any] = []

    async def ko_get(path: str, transport: Any = None) -> Any:
        assert path == "/api/household/9/shoppinglist"
        return LISTS

    async def add_item(request: Any, name: str, description: str = "", transport: Any = None,
                       list_id: int | None = None) -> dict[str, Any]:  # fmt: skip
        added.append((name, description, list_id))
        return {"added": True, "already": False, "item": {"key": "ko:77", "name": name}}

    real = httpx.AsyncClient

    def client(*a: Any, **kw: Any) -> httpx.AsyncClient:
        return real(transport=shops([]))

    monkeypatch.setattr(S, "kitchenowl_get", ko_get)
    monkeypatch.setattr(S, "add_item", add_item)
    monkeypatch.setattr(W.httpx, "AsyncClient", client)
    monkeypatch.setattr(W.asyncio, "sleep", lambda s: _done())
    monkeypatch.setenv("API_TOKEN", "s3cret")
    try:
        app.state.engine = create_async_engine(DB, poolclass=NullPool)  # type: ignore[arg-type]
        h = {"Authorization": "Bearer s3cret"}
        c = TestClient(app)
        r = c.get("/v1/wishlist", headers=h).json()
        assert r["list"] == {"household_id": 9, "list_id": 2, "name": "Wishlist"}  # found by its name
        assert [i["name"] for i in r["items"]] == ["Kamado BBQ", "Ski jacket"] and r["items"][0]["max_price"] == 280.0
        assert r["items"][0]["times_seen"] == 0 and r["last_check"] is None

        out = c.post("/v1/wishlist/check", headers=h).json()
        assert out["errors"] is None and out["wishes"] == 2
        k = next(i for i in out["items"] if i["name"] == "Kamado BBQ")
        # Aldi's £299.99 grill is over the £280 top; Lidl's £249 one counts, and is on sale now
        assert [(s["retailer"], s["status"]) for s in k["now"]] == [("lidl", "on sale")]
        assert k["last_seen"]["name"] == "Grillmeister Kamado Barbecue" and k["last_on_sale"]["on_sale"] == "2026-11-05"
        assert out["last_check"]["matched"] == 1

        # ticked off and made anew in KitchenOwl (a new id): its history comes back by name
        LISTS[1]["items"][0] = {"id": 90, "name": "kamado bbq"}
        again = c.get("/v1/wishlist", headers=h).json()
        assert again["items"][0]["times_seen"] == 1
        assert c.get("/v1/wishlist/ko:90/seen", headers=h).json()["seen"][0]["retailer"] == "lidl"

        assert c.put("/v1/wishlist/settings", json={"list_id": 1}, headers=h).status_code == 422  # not the shopping list
        c.post("/v1/wishlist/items", json={"name": "Air fryer", "description": "under £60"}, headers=h)
        assert added == [("Air fryer", "under £60", 2)]
    finally:
        LISTS[1]["items"][0] = {"id": 77, "name": "Kamado BBQ", "description": "under £280"}
        with sync.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS wishlist_seen"))
            conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        sync.dispose()


async def _done() -> None:
    return None


def test_home_assistant_says_what_is_coming() -> None:
    from app.ha_publish import wishlist_device

    d = wishlist_device({"items": [
        {"name": "Kamado BBQ", "now": [{"retailer": "lidl", "name": "Grillmeister Kamado Barbecue", "price": 249.0,
                                        "status": "on sale", "on_sale": "2026-11-05", "url": "u"}]},
        {"name": "Ski jacket", "now": []}], "last_check": {"at": "2026-11-07T18:40:00+00:00"}})  # fmt: skip
    s = d.sensors[0]
    assert s.state == 1 and s.attributes["items"][0]["wish"] == "Kamado BBQ" and s.attributes["wishes"] == ["Kamado BBQ", "Ski jacket"]
    with pytest.raises(ValueError):
        wishlist_device({"items": []})


async def test_the_tools_say_it_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.wishlist import tools as M

    data = {"list": {"name": "Wishlist"}, "last_check": None, "items": [
        {"key": "ko:77", "name": "Kamado BBQ", "now": [{"retailer": "lidl", "name": "Grillmeister Kamado Barbecue",
                                                        "price": 249.0, "status": "on sale", "on_sale": "2026-11-05"}],
         "last_seen": None, "last_on_sale": None},
        {"key": "ko:78", "name": "Ski jacket", "now": [], "last_seen": {"last_seen": "2026-03-01T18:40:00+00:00"},
         "last_on_sale": {"on_sale": "2026-02-26", "price": 39.99}},
        {"key": "ko:79", "name": "Air fryer", "now": [], "last_seen": None, "last_on_sale": None}]}  # fmt: skip

    async def wishes(request: Any) -> dict[str, Any]:
        return data

    monkeypatch.setattr(W, "wishes", wishes)
    t = (await M.wishlist_tool(object(), {}))["text"]  # type: ignore[arg-type]
    assert t == ("Kamado BBQ: Grillmeister Kamado Barbecue at Lidl at £249.00, on sale now. "
                 "Ski jacket: not on sale now. Last seen 2026-03-01; last on sale 2026-02-26 at £39.99. "
                 "Air fryer: not seen yet.")  # fmt: skip
    assert (await M.wishlist_tool(object(), {"name": "ski"}))["text"].startswith("Ski jacket:")  # type: ignore[arg-type]
    gone: list[str] = []

    async def remove(request: Any, key: str) -> None:
        gone.append(key)

    monkeypatch.setattr(W, "remove_wish", remove)
    r = await M.wish_remove_tool(object(), {"name": "air"})  # type: ignore[arg-type]
    assert gone == ["ko:79"] and "Ticked Air fryer off" in r["text"]
    json.dumps(r)


def test_the_shops_are_asked_for_the_distinctive_words() -> None:
    from app.modules.wishlist.sources.wishlist import clients as W

    assert W.search_words("kamado bbq") == "kamado"
    assert W.search_words("Large garden grill set") == "garden"
    assert W.search_words("bbq") == "bbq"  # nothing distinctive: as it is
    today = W.uk_today()
    shop_only = W.product("aldi", {"name": "Kamado Grill", "sku": "1", "price": {"amount": 29999}, "notForSale": True}, today)
    assert shop_only and shop_only["status"] != "sold out"  # in shops only, not sold online: not sold out

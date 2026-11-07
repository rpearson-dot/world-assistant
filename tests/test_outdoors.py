"""Outdoors: GO Outdoors (Shopify) and Decathlon (a pasted page) read, and prices remembered so a drop shows."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

import httpx
import pytest

from app import outdoors as OD
from app import shopping as S
from app.integrations.outdoors import clients as O
from app.integrations.wishlist.clients import ShopError

SUGGEST = {"resources": {"results": {"products": [
    {"id": 1, "title": "Eurohike Avon 2 Tent", "handle": "eurohike-avon-2-tent", "vendor": "Eurohike",
     "url": "/products/eurohike-avon-2-tent?_pos=1", "price": "45.00", "compare_at_price_max": "60.00", "available": True},
    {"id": 2, "title": "Vango Soul 200 Tent", "handle": "vango-soul-200", "price": "120.00", "available": False},
    {"id": 3, "title": "Tent Footprint Groundsheet", "handle": "footprint", "price": "15.00", "available": True},
    {"id": 4, "title": "Tent Repair Spares Kit", "handle": "tent-spares", "price": "8.00", "available": True},
]}}}  # fmt: skip
PRODUCT_JS = {"title": "Vango Soul 200 Tent", "handle": "vango-soul-200", "vendor": "Vango", "price": 11000,
              "compare_at_price": 15000, "available": True}  # fmt: skip
DECATHLON_HTML = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@graph":[
{"@type":"BreadcrumbList"},{"@type":"Product","name":"Quechua MH100 2-person tent","sku":"8492","brand":{"@type":"Brand","name":"Quechua"},
"offers":{"@type":"Offer","price":"39.99","priceCurrency":"GBP","availability":"https://schema.org/InStock"}}]}</script></head></html>"""


def shops(seen: list[str], go: Any = None, decathlon: str = DECATHLON_HTML) -> httpx.MockTransport:
    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host.endswith("decathlon.co.uk"):
            return httpx.Response(200, text=decathlon)
        if req.url.path.endswith(".js"):
            return httpx.Response(200, json=go or PRODUCT_JS)
        return httpx.Response(200, json=SUGGEST)

    return httpx.MockTransport(answer)


def test_links_in_a_description() -> None:
    d = ("under £150 https://www.gooutdoors.co.uk/products/vango-soul-200?variant=1, and "
         "https://www.decathlon.co.uk/p/mh100-tent/_/R-p-8492?mc=8492. https://example.com/x")  # fmt: skip
    assert O.links(d) == [("gooutdoors", "https://www.gooutdoors.co.uk/products/vango-soul-200"),
                          ("decathlon", "https://www.decathlon.co.uk/p/mh100-tent/_/R-p-8492?mc=8492")]  # fmt: skip
    assert O.links("just under £100") == []


async def test_go_outdoors_and_decathlon_are_read() -> None:
    seen: list[str] = []
    async with httpx.AsyncClient(transport=shops(seen)) as c:
        found = await O.go_search(c, "2 man tent")
        one = await O.go_product(c, "https://www.gooutdoors.co.uk/products/vango-soul-200")
        dec = await O.decathlon_product(c, "https://www.decathlon.co.uk/p/mh100-tent/_/R-p-8492")
    assert "resources%5Btype%5D=product" in seen[0] and seen[1].endswith("/products/vango-soul-200.js")
    assert found[0] == {"retailer": "gooutdoors", "id": "eurohike-avon-2-tent", "name": "Eurohike Avon 2 Tent",
                        "brand": "Eurohike", "price": 45.0, "was": 60.0, "available": True,
                        "url": "https://www.gooutdoors.co.uk/products/eurohike-avon-2-tent"}  # fmt: skip
    assert found[1]["available"] is False
    assert (one["price"], one["was"], one["brand"]) == (110.0, 150.0, "Vango")  # pence in the product JSON
    assert (dec["name"], dec["price"], dec["available"], dec["brand"], dec["id"]) == (
        "Quechua MH100 2-person tent", 39.99, True, "Quechua", "8492")
    async with httpx.AsyncClient(transport=shops([], decathlon="<html>blocked</html>")) as c:
        with pytest.raises(ShopError, match="no product data"):
            await O.decathlon_product(c, "https://www.decathlon.co.uk/p/x")


async def test_a_search_follows_the_three_cheapest_real_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(OD.asyncio, "sleep", _now)
    async with httpx.AsyncClient(transport=shops([])) as c:
        got, errs = await OD.read(c, {"name": "Tent", "links": [], "must": []})
    # the footprint counts (it has "tent"); the spares kit is an accessory, so it does not
    assert [p["id"] for p in got] == ["footprint", "eurohike-avon-2-tent", "vango-soul-200"] and errs == []
    async with httpx.AsyncClient(transport=shops([])) as c:
        got, _ = await OD.read(c, {"name": "Tent", "links": [], "must": ["vango"]})
    assert [p["id"] for p in got] == ["vango-soul-200"]


async def _now(*a: Any) -> None:
    return None


def test_the_page_script_compiles_and_calls_real_routes(tmp_path: Any) -> None:
    from app.main import app
    from app.outdoors_page import PAGE

    js = re.findall(r"<script>(.*?)</script>", PAGE, re.DOTALL)[-1]
    f = tmp_path / "o.js"
    f.write_text(js)
    subprocess.run(["node", "--check", str(f)], check=True)
    paths = set(app.openapi()["paths"])
    for p in set(re.findall(r"'(/v1/outdoors[^']*)'", PAGE)):
        p = p.rstrip("/")
        assert any(x == p or x.startswith(p + "/") for x in paths), p


def test_home_assistant_counts_the_drops() -> None:
    from app.ha_publish import outdoors_device

    d = outdoors_device({"items": [{"name": "Tent", "dropped": [{"name": "Vango Soul 200", "retailer": "gooutdoors",
                                                                 "price": 99.0, "previous_price": 110.0}]},
                                   {"name": "Stove", "dropped": []}]})  # fmt: skip
    s = d.sensors[0]
    assert s.state == 1 and s.attributes["drops"][0]["from"] == 110.0 and s.attributes["items"] == ["Tent", "Stove"]


DB = os.environ.get("WA_TEST_DATABASE_URL")
LISTS = [{"id": 1, "name": "Shopping", "items": []},
         {"id": 3, "name": "Outdoors", "items": [{"id": 50, "name": "Vango tent",
                                                  "description": "https://www.gooutdoors.co.uk/products/vango-soul-200 under £150"}]}]  # fmt: skip


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_prices_are_remembered_and_a_fall_is_a_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.main import app

    sync = sa.create_engine(DB)  # type: ignore[arg-type]
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("m0039", Path("migrations/versions/0039_outdoors.py"))
    mig = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mig)  # type: ignore[union-attr]
    with sync.begin() as conn:
        for t in ("outdoors_price", "outdoors_product", "app_setting"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null,"
                          " updated_at timestamptz not null default now())"))  # fmt: skip
        conn.execute(text("INSERT INTO app_setting (key, value) VALUES (:k, CAST(:v AS jsonb))"),
                     {"k": S.SETTINGS_KEY, "v": json.dumps({"household_id": 9, "list_id": 1})})
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    price = {"now": 11000}

    async def ko_get(path: str, transport: Any = None) -> Any:
        return LISTS

    real = httpx.AsyncClient

    def client(*a: Any, **kw: Any) -> httpx.AsyncClient:
        return real(transport=shops([], go={**PRODUCT_JS, "price": price["now"]}))

    monkeypatch.setattr(S, "kitchenowl_get", ko_get)
    monkeypatch.setattr(OD.httpx, "AsyncClient", client)
    monkeypatch.setattr(OD.asyncio, "sleep", _now)
    monkeypatch.setenv("API_TOKEN", "s3cret")
    try:
        app.state.engine = create_async_engine(DB, poolclass=NullPool)  # type: ignore[arg-type]
        h = {"Authorization": "Bearer s3cret"}
        c = TestClient(app)
        first = c.post("/v1/outdoors/check", headers=h).json()
        it = first["items"][0]
        assert first["list"]["name"] == "Outdoors" and it["max_price"] == 150.0 and it["links"][0][0] == "gooutdoors"
        p = it["products"][0]
        assert (p["price"], p["was_price"], p["lowest_price"], p["pinned"], p["dropped"]) == (110.0, 150.0, 110.0, True, False)
        assert first["drops"] == 0 and it["under"]  # £110 is under the £150 asked

        price["now"] = 9900
        second = c.post("/v1/outdoors/check", headers=h).json()
        p = second["items"][0]["products"][0]
        assert second["drops"] == 1 and second["fell"][0]["from"] == 110.0
        assert (p["price"], p["previous_price"], p["lowest_price"], p["dropped"]) == (99.0, 110.0, 99.0, True)

        price["now"] = 10500  # back up a bit: still a drop this week, the lowest stays
        third = c.post("/v1/outdoors/check", headers=h).json()
        p = third["items"][0]["products"][0]
        assert third["drops"] == 0 and (p["price"], p["previous_price"], p["lowest_price"], p["dropped"]) == (105.0, 99.0, 99.0, True)
        hist = c.get("/v1/outdoors/ko:50/history", headers=h).json()["prices"]
        assert [x["price"] for x in hist] == [105.0, 99.0, 110.0]
        same = c.post("/v1/outdoors/check", headers=h).json()  # no change: no new history
        assert same["drops"] == 0 and len(c.get("/v1/outdoors/ko:50/history", headers=h).json()["prices"]) == 3
    finally:
        with sync.begin() as conn:
            for t in ("outdoors_price", "outdoors_product", "app_setting"):
                conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        sync.dispose()


BIKESY_CARDS = """<div class="grid">
<div class="product"><a href="https://bikesy.co.uk/outbound.php?pid=5300528"><img src="x.jpg" alt="Shimano 105 R7100 12 Speed Cassette 11-34T"></a>
  <span class="badge">38% OFF</span><span class="price">&pound;54.99</span><span class="rrp">&pound;89.99</span>
  <span>SAVE &pound;35.00</span><a href="https://bikesy.co.uk/outbound.php?pid=5300528">View Deal</a></div>
<div class="product"><a href="https://bikesy.co.uk/outbound.php?pid=5300601"><img src="y.jpg"></a>
  <h3>Shimano 105 CS-R7100 Cassette 11-30T</h3><span>£59.00</span><a href="https://bikesy.co.uk/outbound.php?pid=5300601">View Deal</a></div>
<div class="product"><a href="https://bikesy.co.uk/outbound.php?pid=5300777"><img src="z.jpg" alt="Shimano 105 Chain Tool"></a>
  <span>£1,249.00</span></div>
</div>"""


def test_bikesy_deals_are_read_from_its_listing() -> None:
    got = O.bikesy_deals(BIKESY_CARDS)
    assert [(d["id"], d["name"], d["price"], d["was"]) for d in got] == [
        ("5300528", "Shimano 105 R7100 12 Speed Cassette 11-34T", 54.99, 89.99),  # the SAVE figure is left out
        ("5300601", "Shimano 105 CS-R7100 Cassette 11-30T", 59.0, None),  # no image text: the deal's own name
        ("5300777", "Shimano 105 Chain Tool", 1249.0, None)]  # fmt: skip
    assert got[0]["url"] == "https://bikesy.co.uk/outbound.php?pid=5300528" and got[0]["retailer"] == "bikesy"
    assert O._tag("Shimano 105 cassette") == "shimano+105+cassette"


async def test_a_search_also_asks_bikesy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(OD.asyncio, "sleep", _now)
    seen: list[str] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host == "bikesy.co.uk":
            return httpx.Response(200, text=BIKESY_CARDS)
        return httpx.Response(200, json={"resources": {"results": {"products": []}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as c:
        got, errs = await OD.read(c, {"name": "105 cassette", "links": [], "must": []})
    assert seen[1] == "https://bikesy.co.uk/tag/105+cassette/"  # the tag page: its /s/ search is closed to robots
    assert [p["id"] for p in got] == ["5300528", "5300601"] and errs == []  # the chain tool is not a cassette

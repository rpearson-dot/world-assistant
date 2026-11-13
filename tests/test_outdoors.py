"""Outdoors: GO Outdoors (Shopify) and Decathlon (a pasted page) read, and prices remembered so a drop shows."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

import httpx
import pytest

from app.modules.outdoors import outdoors as OD
from app.modules.outdoors.sources.outdoors import clients as O
from app.modules.shopping import shopping as S
from app.modules.wishlist.sources.wishlist.clients import ShopError

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
        got, errs, _ = await OD.read(c, {"name": "Tent", "links": [], "must": []})
    # the footprint counts (it has "tent"); the spares kit is an accessory, so it does not
    assert [p["id"] for p in got] == ["footprint", "eurohike-avon-2-tent", "vango-soul-200"] and errs == []
    async with httpx.AsyncClient(transport=shops([])) as c:
        got, _, _ = await OD.read(c, {"name": "Tent", "links": [], "must": ["vango"]})
    assert [p["id"] for p in got] == ["vango-soul-200"]


async def _now(*a: Any) -> None:
    return None


def test_the_page_script_compiles_and_calls_real_routes(tmp_path: Any) -> None:
    from app.main import app
    from app.modules.outdoors.outdoors_page import PAGE

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

    migs = []
    for f in ("0039_outdoors.py", "0041_outdoors_currency.py", "0042_outdoors_shop.py"):
        spec = importlib.util.spec_from_file_location(f[:5], Path("migrations/versions") / f)
        m = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(m)  # type: ignore[union-attr]
        migs.append(m)
    with sync.begin() as conn:
        for t in ("outdoors_price", "outdoors_product", "app_setting"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null,"
                          " updated_at timestamptz not null default now())"))  # fmt: skip
        conn.execute(text("INSERT INTO app_setting (key, value) VALUES (:k, CAST(:v AS jsonb))"),
                     {"k": S.SETTINGS_KEY, "v": json.dumps({"household_id": 9, "list_id": 1})})
        with Operations.context(MigrationContext.configure(conn)):
            for m in migs:
                m.upgrade()
    price = {"now": 11000}
    status = {"code": 200}

    async def ko_get(path: str, transport: Any = None) -> Any:
        return LISTS

    real = httpx.AsyncClient

    def client(*a: Any, **kw: Any) -> httpx.AsyncClient:
        if status["code"] != 200:
            return real(transport=httpx.MockTransport(lambda r: httpx.Response(status["code"])))
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
        assert same["items"][0]["products"][0]["currency"] == "GBP" and same["last_check"]["sources"] == {"gooutdoors": "ok"}

        status["code"] = 503  # GO Outdoors is down: the product keeps its last price, and says why
        down = c.post("/v1/outdoors/check", headers=h).json()
        p = down["items"][0]["products"][0]
        assert p["price"] == 105.0 and "HTTP 503" in p["error"] and p["error_at"] and down["items"][0]["failing"]
        assert down["last_check"]["sources"]["gooutdoors"].startswith("failed: ")
        status["code"] = 200  # back: the error clears
        back = c.post("/v1/outdoors/check", headers=h).json()
        assert back["items"][0]["products"][0]["error"] is None and back["last_check"]["sources"] == {"gooutdoors": "ok"}
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
        got, errs, _ = await OD.read(c, {"name": "105 cassette", "links": [], "must": []})
    assert seen[1] == "https://bikesy.co.uk/tag/105+cassette/"  # the tag page: its /s/ search is closed to robots
    assert [p["id"] for p in got] == ["5300528", "5300601"] and errs == []  # the chain tool is not a cassette


TRADEINN = {"total": {"value": 2}, "id_modelos": [
    {"id_modelo": 137201, "marca": "Shimano", "nombre_modelo_eng": "105 R7100 12s Cassette", "precio_win": "54.99",
     "productes": [{"baja": 0, "rec": "89.99", "exist": 4, "stock_reservat": 0, "sellers": [{"id_seller": 1, "precio_producte": "54.99"}]}]},
    {"id_modelo": 137300, "marca": "Sram", "nombre_modelo_eng": "Sram PG-1130 Cassette", "precio_win": None,
     "productes": [{"baja": 0, "rec": "60", "exist": 0, "stock_reservat": 0, "sellers": [{"id_seller": 2, "precio_producte": "41.50"}]}]},
]}  # fmt: skip


def test_tradeinn_products_are_read() -> None:
    got = O.tradeinn_products(TRADEINN, "bikeinn")
    a, b = got
    assert (a["id"], a["name"], a["price"], a["was"], a["available"], a["currency"]) == (
        "bikeinn:137201", "Shimano 105 R7100 12s Cassette", 54.99, 89.99, True, "EUR")  # no currency said: euros
    assert a["url"] == "https://www.tradeinn.com/bikeinn/en/105-r7100-12s-cassette/137201/p"
    assert (b["name"], b["price"], b["available"]) == ("Sram PG-1130 Cassette", 41.5, False)  # the seller's price; none in stock
    assert O.tradeinn_products({**TRADEINN, "moneda": "gbp"}, "bikeinn")[0]["currency"] == "GBP"
    with pytest.raises(ShopError, match="something other than its search answer"):
        O.tradeinn_products([1, 2], "bikeinn")


async def test_tradeinn_is_asked_as_its_page_asks_and_failures_are_words() -> None:
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=TRADEINN)

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as c:
        got = await O.tradeinn_search(c, "105 cassette", shop="bikeinn")
    r = seen[0]
    assert r.method == "POST" and r.url.params["action"] == "get_info_elastic_listado" and r.url.params["id_tienda"] == "4"
    assert r.headers["Cookie"] == "id_pais=209;"  # delivered to the UK
    body = r.content.decode()
    assert body.startswith("vars%5B%5D=id_tienda=4@fecha_descatalogado=[now/d-*]") and body.endswith("texto_search=105%20cassette")
    assert len(got) == 2
    for resp, words in ((httpx.Response(403), "refused"), (httpx.Response(200, text="<html>Just a moment... cloudflare challenge"), "challenge page"),
                        (httpx.Response(200, text="<html>oops"), "other than JSON"), (httpx.Response(500), "HTTP 500")):  # fmt: skip
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda q, x=resp: x)) as c:
            with pytest.raises(ShopError, match=words):
                await O.tradeinn_search(c, "x", shop="trekkinn")
    async with httpx.AsyncClient() as c:
        with pytest.raises(ShopError, match="no shop called"):
            await O.tradeinn_search(c, "x", shop="nowhereinn")


async def test_a_shop_that_fails_is_asked_once_a_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(OD.asyncio, "sleep", _now)
    asked: list[str] = []

    def answer(req: httpx.Request) -> httpx.Response:
        asked.append(req.url.host)
        if req.url.host == "www.tradeinn.com":
            return httpx.Response(403)
        if req.url.host == "bikesy.co.uk":
            return httpx.Response(200, text="<html>no deals</html>")
        return httpx.Response(200, json={"resources": {"results": {"products": []}}})

    down: dict[str, str] = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as c:
        _, errs1, _ = await OD.read(c, {"name": "tent", "links": [], "must": []}, down)
        _, errs2, _ = await OD.read(c, {"name": "stove", "links": [], "must": []}, down)
    assert asked.count("www.tradeinn.com") == 2  # trekkinn and bikeinn once each, for the first item only
    assert set(down) == {"tradeinn:trekkinn", "tradeinn:bikeinn"} and len(errs1) == 2 and errs2 == []
    # a followed link that fails is reported for that product; a dead link does not take the shop down
    def dead(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    down = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(dead)) as c:
        got, errs, failed = await OD.read(c, {"name": "tent", "links": [("gooutdoors", "https://www.gooutdoors.co.uk/products/x")], "must": []}, down)
    assert got == [] and failed == [("https://www.gooutdoors.co.uk/products/x", errs[0])] and down == {}


# --- Sigma Sports --------------------------------------------------------------------------------------------------
SIGMA_PAGE = """<html><head><title>Shimano 105 11-Speed Cassette R7000/HG700</title></head><body>
<h1 class="t">Shimano 105 11-Speed <span>Bike Cassette</span> R7000/HG700</h1>
<div class="price"><span>&pound;37.00</span> <s>&pound;64.99</s> <em>Save 43%</em></div>
<p>In stock, available for delivery</p><p>Collect in Store: select a store to check availability.</p>
<div>Customers also bought: <a href="/item/Shimano/Chain/ABC1">Chain</a> £19.99</div></body></html>"""
SIGMA_LD = ('<script type="application/ld+json">{"@type": "Product", "name": "Rapha Core Jersey", "brand": {"name": "Rapha"},'
            ' "offers": {"price": "60.00", "priceCurrency": "GBP", "availability": "https://schema.org/OutOfStock"}}</script>'
            "<h1>Rapha Core Jersey</h1>")  # fmt: skip
SIGMA_LIST = """<ul>
<li><a href="/item/Shimano/105-HG700-11-speed-Cassette-11-34T/IST8"><img alt="Shimano 105 HG700 11-Speed Cassette" src="x.jpg"></a>
  <a href="/item/Shimano/105-HG700-11-speed-Cassette-11-34T/IST8">Shimano 105 HG700</a> <span>£37.00</span> <s>£64.99</s> Save £27.99</li>
<li><a href="https://www.sigmasports.com/item/SRAM/PG-1130-Cassette/Z8DD?colour=x">SRAM PG-1130</a> <span>£41.50</span></li>
<li><a href="/item/Shimano/L05A-RF-Disc-Brake-Pads-Resin/XWYG">Pads</a> no price shown</li>
</ul>"""


def test_sigma_links_pages_and_listings() -> None:
    assert O.links("collect from Hampton Wick https://www.sigmasports.com/item/Shimano/105-HG700/IST8?size=11-34 ok") == [
        ("sigma", "https://www.sigmasports.com/item/Shimano/105-HG700/IST8")]
    assert O.links("https://www.sigmasports.com/components/brakes") == []  # a category page is not one product
    assert O.links("https://notsigmasports.com/item/a/b/C https://evil.example/decathlon.co.uk/p/1") == []  # look-alikes
    p = O.sigma_page(SIGMA_PAGE, "https://www.sigmasports.com/item/Shimano/105-HG700/IST8")
    assert (p["id"], p["name"], p["price"], p["was"], p["available"], p["brand"]) == (
        "IST8", "Shimano 105 11-Speed Bike Cassette R7000/HG700", 37.0, 64.99, True, "Shimano")
    ld = O.sigma_page(SIGMA_LD, "https://www.sigmasports.com/item/Rapha/Core-Jersey/R1")
    assert (ld["name"], ld["price"], ld["brand"], ld["available"]) == ("Rapha Core Jersey", 60.0, "Rapha", False)
    assert O.sigma_page("<html>nothing</html>", "https://www.sigmasports.com/item/a/b/C") is None
    got = O.sigma_listing(SIGMA_LIST)
    assert [(g["id"], g["name"], g["price"], g["was"]) for g in got] == [
        ("IST8", "Shimano 105 HG700 11-Speed Cassette", 37.0, 64.99),  # the Save figure left out
        ("Z8DD", "SRAM PG 1130 Cassette", 41.5, None)]  # named from its address; the pads have no price  # fmt: skip
    assert got[1]["url"] == "https://www.sigmasports.com/item/SRAM/PG-1130-Cassette/Z8DD"


async def test_sigma_search_is_found_kept_and_follows_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    def site(robots: str, works: str) -> httpx.MockTransport:
        def answer(req: httpx.Request) -> httpx.Response:
            asked.append(str(req.url).replace(O.SIGMA, ""))
            if req.url.path == "/robots.txt":
                return httpx.Response(200, text=robots)
            if str(req.url).startswith(O.SIGMA + works):
                return httpx.Response(200, text=SIGMA_LIST)
            return httpx.Response(404) if req.url.path.startswith("/search/") else httpx.Response(200, text="<html>home</html>")
        return httpx.MockTransport(answer)

    for robots, works, first in (("", "/search?query=", "/search?q=105+cassette"),
                                 ("User-agent: *\nDisallow: /search?q=", "/search/", "/search?query=105+cassette")):  # fmt: skip
        monkeypatch.setattr(O, "_robots", {})
        monkeypatch.setattr(O, "_sigma_search", {})
        asked: list[str] = []
        async with httpx.AsyncClient(transport=site(robots, works)) as c:
            got = await O.sigma_search(c, "105 cassette")
            assert [g["id"] for g in got] == ["IST8", "Z8DD"] and asked[:2] == ["/robots.txt", first]
            asked.clear()
            await O.sigma_search(c, "cassette")  # the address that worked is kept: one request
            assert len(asked) == 1 and asked[0].startswith(works)
    monkeypatch.setattr(O, "_robots", {})
    monkeypatch.setattr(O, "_sigma_search", {})
    async with httpx.AsyncClient(transport=site("User-agent: *\nDisallow: /search", "/none")) as c:
        with pytest.raises(ShopError, match="robots.txt closes its search"):
            await O.sigma_search(c, "105 cassette")
    monkeypatch.setattr(O, "_robots", {})
    async with httpx.AsyncClient(transport=site("", "/none")) as c:
        assert await O.sigma_search(c, "105 cassette") == []  # pages came back without it: nothing found, not a failure


def test_bikesy_deals_name_their_shop_and_the_preferred_shop_is_shown() -> None:
    from app.assistant_more import _outdoor_text

    html = BIKESY_CARDS.replace("outbound.php?pid=5300601", 'outbound.php?pid=5300601"><img src="/logos/sigma-sports.png"')
    got = {d["id"]: d.get("shop") for d in O.bikesy_deals(html)}
    assert got["5300601"] == "Sigma Sports" and got["5300528"] != "Sigma Sports"
    assert O.shop_in("<img alt='Merlin Cycles'>") == "Merlin Cycles" and O.shop_in("nothing") is None
    cheap = {"name": "Cassette A", "retailer": "gooutdoors", "price": 30.0, "currency": "GBP"}
    others = [{"name": f"Cassette {x}", "retailer": "bikesy", "price": 31.0 + i, "currency": "GBP"} for i, x in enumerate("BC")]
    sigma = {"name": "Cassette S", "retailer": "bikesy", "shop": "Sigma Sports", "price": 37.0, "currency": "GBP"}
    text = _outdoor_text({"name": "105 cassette", "products": [cheap, *others, sigma], "preferred": [sigma]})
    assert text.endswith("Cassette S at Sigma Sports (via Bikesy), £37.00.")  # fourth by price, but shown
    assert OD.shop_of(sigma) == "Sigma Sports" and OD.shop_of({"retailer": "sigma"}) == "Sigma Sports"

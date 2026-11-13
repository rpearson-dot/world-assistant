"""Shopping: quantities, choosing a product, totals, and the KitchenOwl and supermarket clients."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.modules.shopping import shopping as S


@pytest.mark.parametrize(("desc", "want"), [
    ("", (1, "")), ("2", (2, "")), ("x3", (3, "")), ("3x", (3, "")), ("2 tins", (2, "")), ("4 packs", (4, "")),
    ("2x semi-skimmed", (2, "semi-skimmed")), ("3 tins chopped", (3, "chopped")),
    ("500g", (1, "500g")), ("semi-skimmed 4 pints", (1, "semi-skimmed 4 pints")), ("4 pints", (1, "4 pints")),
    ("150", (1, "150")),  # three digits is not a count anyone means
])  # fmt: skip
def test_quantity_reads_counts_and_leaves_descriptions(desc: str, want: tuple[int, str]) -> None:
    assert S.quantity(desc) == want


def test_query_is_the_name_plus_what_describes_it() -> None:
    assert S.query_for("Milk", "semi-skimmed 4 pints") == "Milk semi-skimmed 4 pints"
    assert S.query_for("  Eggs ", "") == "Eggs"
    assert len(S.query_for("x" * 200, "")) == 120


def P(pid: str, price: float | None, stock: bool | None = True) -> dict[str, Any]:
    return {"id": pid, "name": f"name {pid}", "price": price, "in_stock": stock}


def test_best_skips_out_of_stock_and_unpriced() -> None:
    assert S.best([P("a", None), P("b", 1.0, False), P("c", 2.0, None), P("d", 0.5)])["id"] == "c"
    assert S.best([P("a", None)]) is None


def test_choose_prefers_your_usual_and_says_when_it_is_gone() -> None:
    found = [P("a", 1.0), P("b", 2.0)]
    assert S.choose(found, {"id": "b"}) == (found[1], "usual")
    assert S.choose(found, {"id": "z"}) == (found[0], "usual_missing")
    assert S.choose(found, None) == (found[0], "best")
    assert S.choose([], None) == (None, "none")
    assert S.choose([], {"id": "z"}) == (None, "none")


def test_price_items_totals_and_the_cheaper_shop() -> None:
    items = [
        {"key": "ko:1", "name": "Milk", "description": "2"},
        {"key": "ko:2", "name": "Eggs", "description": ""},
        {"key": "ko:3", "name": "Saffron", "description": ""},
    ]
    found = {
        ("sainsburys", "Milk"): {"products": [P("s-milk", 1.50)], "error": None},
        ("ocado", "Milk"): {"products": [P("o-milk", 1.45)], "error": None},
        ("sainsburys", "Eggs"): {"products": [P("s-eggs", 2.00)], "error": None},
        ("ocado", "Eggs"): {"products": [P("o-eggs", 2.40), P("o-eggs-cheap", 1.90)], "error": None},
        ("sainsburys", "Saffron"): {"products": [P("s-saff", 3.00)], "error": None},
        ("ocado", "Saffron"): {"products": [], "error": "blocked"},
        ("ocado", "name o-eggs-cheap"): {"products": [P("o-eggs-cheap", 1.85)], "error": None},
    }
    usual = {("ko:2", "ocado"): {"id": "o-eggs-cheap", "name": "name o-eggs-cheap"}}
    rows = S.price_items(items, ["sainsburys", "ocado"], found, usual)
    milk, eggs, saff = rows
    assert milk["quantity"] == 2 and milk["retailers"]["sainsburys"]["line"] == 3.0
    assert eggs["retailers"]["ocado"]["why"] == "usual" and eggs["retailers"]["ocado"]["line"] == 1.90  # from the first search
    assert saff["retailers"]["ocado"]["choice"] is None and saff["retailers"]["ocado"]["error"] == "blocked"
    t = S.totals(rows, ["sainsburys", "ocado"])
    assert t["by_retailer"]["sainsburys"] == {"label": "Sainsbury's", "total": 8.0, "items": 3, "missing": 0, "like_for_like": 5.0}
    assert t["by_retailer"]["ocado"]["total"] == 4.8 and t["by_retailer"]["ocado"]["missing"] == 1
    assert t["compared_items"] == 2 and t["cheaper"] == "ocado" and t["saving"] == 0.2


def test_totals_with_one_retailer_or_a_tie_names_no_winner() -> None:
    row = {"retailers": {"sainsburys": {"choice": P("a", 1.0), "line": 1.0}, "ocado": {"choice": P("b", 1.0), "line": 1.0}}}
    assert S.totals([row], ["sainsburys", "ocado"])["cheaper"] is None
    assert S.totals([row], ["sainsburys"])["cheaper"] is None


KO_LISTS = [
    {"id": 7, "name": "Shopping", "items": [
        {"id": 11, "name": "Milk", "description": "2", "category": {"name": "Dairy"}},
        {"id": 12, "name": " ", "description": ""},
        {"id": 13, "name": "Bread", "description": None, "category": None},
    ]},
    {"id": 8, "name": "Hardware", "items": []},
]  # fmt: skip


def test_list_items_keys_by_kitchenowl_item_and_drops_blank_names() -> None:
    assert S.list_items(KO_LISTS, 7) == [
        {"key": "ko:11", "name": "Milk", "description": "2", "category": "Dairy"},
        {"key": "ko:13", "name": "Bread", "description": "", "category": None},
    ]
    with pytest.raises(HTTPException) as e:
        S.list_items(KO_LISTS, 99)
    assert e.value.status_code == 409


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    secrets = {"kitchenowl_token": "ko-token", "supermarket_api_token": "sm-token"}
    monkeypatch.setenv("WA_KITCHENOWL_URL", "http://kitchenowl:8080/")
    monkeypatch.setenv("WA_SUPERMARKET_URL", "http://wa-supermarket:7876")
    monkeypatch.setattr(S, "read_secret", lambda name, default="": secrets.get(name, default))
    get_settings.cache_clear()
    yield secrets
    get_settings.cache_clear()


async def test_kitchenowl_is_called_with_the_token(wired: dict[str, str]) -> None:
    seen = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append((str(req.url), req.headers["authorization"]))
        return httpx.Response(200, json=KO_LISTS)

    got = await S.kitchenowl_get("/api/household/1/shoppinglist", httpx.MockTransport(answer))
    assert got == KO_LISTS
    assert seen == [("http://kitchenowl:8080/api/household/1/shoppinglist", "Bearer ko-token")]


@pytest.mark.parametrize(("status", "code", "words"), [(401, 409, "refused the token"), (500, 502, "HTTP 500")])
async def test_kitchenowl_errors_say_what_to_do(wired: dict[str, str], status: int, code: int, words: str) -> None:
    with pytest.raises(HTTPException) as e:
        await S.kitchenowl_get("/api/household", httpx.MockTransport(lambda r: httpx.Response(status, json={})))
    assert e.value.status_code == code and words in e.value.detail


async def test_not_installed_or_no_token_is_409_not_a_crash(monkeypatch: pytest.MonkeyPatch, wired: dict[str, str]) -> None:
    wired.pop("kitchenowl_token")
    with pytest.raises(HTTPException) as e:
        await S.kitchenowl_get("/api/household")
    assert e.value.status_code == 409 and "token" in e.value.detail
    monkeypatch.setenv("WA_KITCHENOWL_URL", "")
    monkeypatch.setenv("WA_SUPERMARKET_URL", "")
    get_settings.cache_clear()
    for call in (S.kitchenowl_get("/api/household"), S.service("GET", "/health")):
        with pytest.raises(HTTPException) as e:
            await call
        assert e.value.status_code == 409 and "not set up" in e.value.detail


async def test_kitchenowl_unreachable_is_a_503_with_a_reason(wired: dict[str, str]) -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(HTTPException) as e:
        await S.kitchenowl_get("/api/household", httpx.MockTransport(down))
    assert e.value.status_code == 503 and "container" in e.value.detail


async def test_the_supermarket_service_gets_its_token_and_its_errors_come_through(wired: dict[str, str]) -> None:
    seen = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), req.headers["authorization"], json.loads(req.content or b"null")))
        if req.url.path == "/login/code":
            return httpx.Response(401, json={"error": "Sainsbury's did not accept that code."})
        return httpx.Response(200, json={"results": []})

    t = httpx.MockTransport(answer)
    assert await S.service("POST", "/compare", {"items": [{"query": "milk"}]}, transport=t) == {"results": []}
    assert seen[0][:3] == ("POST", "http://wa-supermarket:7876/compare", "Bearer sm-token")
    with pytest.raises(HTTPException) as e:
        await S.service("POST", "/login/code", {"code": "000000"}, transport=t)
    assert e.value.status_code == 401 and "did not accept" in e.value.detail


async def test_a_service_failure_is_a_502_with_its_message(wired: dict[str, str]) -> None:
    t = httpx.MockTransport(lambda r: httpx.Response(504, json={"error": "Ocado search took longer than 25 s"}))
    with pytest.raises(HTTPException) as e:
        await S.service("GET", "/slots?provider=ocado", transport=t)
    assert e.value.status_code == 502 and "25 s" in e.value.detail


def test_every_address_the_shopping_page_calls_is_a_real_route() -> None:
    """The page builds its URLs as strings; each must match a route (0.27.0 saved keys to a /v1/keys that is not one)."""
    import re as _re

    from app.main import app
    from app.modules.shopping.shopping_page import PAGE

    routes = [(_re.sub(r"\{[^}]+\}", "[^/]+", path), {m.upper() for m in ops}) for path, ops in app.openapi()["paths"].items()]
    calls = _re.findall(r"api\('(GET|PUT|POST|DELETE)', '(/v1/[^']*)'(?: \+ \w+(?: \+ '([^']*)')?)?", PAGE)
    assert len(calls) >= 12
    for method, path, tail in calls:
        url = path.split("?")[0] + ("x" + tail if path.endswith("/") else "")
        assert any(_re.fullmatch(p, url) and method in m for p, m in routes), f"{method} {url} is not a route"


def test_basket_items_are_the_chosen_products_and_quantities_at_that_retailer() -> None:
    last = {"retailers": ["sainsburys", "ocado"], "items": [
        {"name": "Milk", "quantity": 2, "retailers": {"ocado": {"choice": P("o-milk", 1.4)}, "sainsburys": {"choice": None}}},
        {"name": "Eggs", "quantity": 1, "retailers": {"ocado": {"choice": None}, "sainsburys": {"choice": P("s-eggs", 2.0)}}},
    ]}  # fmt: skip
    assert S.basket_items(last, "ocado") == [{"id": "o-milk", "name": "name o-milk", "quantity": 2, "item": "Milk"}]
    assert S.basket_items(last, "sainsburys") == [{"id": "s-eggs", "name": "name s-eggs", "quantity": 1, "item": "Eggs"}]
    assert S.basket_items(None, "ocado") == [] and S.basket_items({"retailers": ["ocado"], "items": []}, "sainsburys") == []


def test_a_retailer_refusing_the_basket_reads_as_log_in_first() -> None:
    e = S._login_first("sainsburys", HTTPException(502, "Request failed with status code 401"))
    assert e.status_code == 409 and "Log in to Sainsbury's first" in e.detail
    other = HTTPException(504, "Ocado basket took longer than 60 s")
    assert S._login_first("ocado", other) is other


def test_the_shopping_page_script_compiles(tmp_path: Any) -> None:
    """A stray escape in the Python string becomes a JavaScript syntax error that blanks the whole page."""
    import re as _re
    import shutil
    import subprocess

    from app.modules.shopping.shopping_page import PAGE

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    scripts = _re.findall(r"<script>(.*?)</script>", PAGE, _re.DOTALL)
    assert scripts
    f = tmp_path / "page.js"
    f.write_text("\n".join(scripts))
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


def test_an_order_covers_items_by_the_product_chosen_or_by_the_same_name() -> None:
    items = [{"key": "ko:1", "name": "Milk"}, {"key": "ko:2", "name": "Free range eggs"}, {"key": "ko:3", "name": "Saffron"}]
    order = {"items": [{"product_id": "s-milk", "name": "Sainsbury's Semi Skimmed 4 pints"},
                       {"product_id": "x", "name": "Free-range eggs"}]}  # fmt: skip
    products = {"ko:1": {"s-milk"}, "ko:3": {"s-saff"}}
    assert S.match_order(order, items, products) == [
        {"key": "ko:1", "name": "Milk", "how": "product"},
        {"key": "ko:2", "name": "Free range eggs", "how": "name"},  # punctuation and case do not matter
    ]
    assert S.match_order({"items": []}, items, products) == []


async def test_removing_items_asks_kitchenowl_once_for_all_of_them(wired: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    async def settings(request: Any) -> dict[str, Any]:
        return {"household_id": 1, "list_id": 7, "retailers": ["ocado"]}

    monkeypatch.setattr(S, "load_settings", settings)
    seen = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"msg": "DONE"})

    n = await S.remove_items(object(), ["ko:12", "ko:3", "ko:12", "bad", "ko:x"], transport=httpx.MockTransport(answer))  # type: ignore[arg-type]
    assert n == 2
    assert seen == [("DELETE", "/api/shoppinglist/7/items", {"items": [{"item_id": 3}, {"item_id": 12}]})]
    assert await S.remove_items(object(), [], transport=httpx.MockTransport(answer)) == 0  # type: ignore[arg-type]
    assert len(seen) == 1  # nothing to remove: nothing sent

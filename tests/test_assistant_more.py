"""The added assistant tools: every feature reachable by an assistant, what each says, and the acting ones."""

from __future__ import annotations

import contextlib
import importlib
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import assistant_base, assistant_more
from app.assistant import TOOLS, ToolError
from app.config import get_settings
from app.main import app
from app.modules import registry
from app.modules.shopping import shopping as S
from app.version import VERSION

# The tools as one namespace, wherever they now live: the core's, and each module's own (app/modules/<id>/tools.py)
_TOOL_MODULES = [importlib.import_module(ref.split(":")[0]) for m in registry.modules()
                 for point, ref in m.provides.items() if point == "assistant.tools"]  # fmt: skip


class _AllTools:
    def __getattr__(self, name: str) -> Any:
        for mod in [*_TOOL_MODULES, assistant_more, assistant_base]:
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(name)


M = _AllTools()

REQ: Any = object()  # the tools only pass it on; every function that would use it is replaced here


def test_every_feature_with_an_api_has_an_assistant_tool() -> None:
    """A module with routes must give the assistant a tool, so a new feature cannot again be API-only by accident."""
    for m in registry.modules():
        if m.routers and not m.core:
            assert m.tools, f"{m.id} has an API but no assistant tool (add one in its tools.py)"
    claimed = {t for m in registry.modules() for t in m.tools}
    assert claimed == set(TOOLS), "every tool belongs to a module, and every module's tool exists"


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_each_tool_schema_is_well_formed(name: str) -> None:
    s = TOOLS[name].schema
    assert s["type"] == "object" and isinstance(s["properties"], dict)
    assert set(s.get("required", [])) <= set(s["properties"]), "a required argument must be described"
    assert TOOLS[name].description.endswith((".", ")")), "descriptions are sentences"


def test_mcp_reports_the_real_version_and_lists_the_new_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    c = TestClient(app)
    h = {"Authorization": "Bearer tok"}
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, headers=h).json()["result"]
    assert r["serverInfo"]["version"] == VERSION and "send_to_basket" in r["instructions"]
    assert c.get("/openapi.json").json()["info"]["version"] == VERSION
    tools = {t["name"] for t in c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=h).json()["result"]["tools"]}
    assert tools >= {"shopping", "add_to_list", "price_list", "send_to_basket", "floods_near", "road_disruptions_near",
                     "live_buses_near", "bus_disruptions", "stops_near", "connections", "postcode", "add_place", "add_trip"}  # fmt: skip


@pytest.fixture
def at_home(monkeypatch: pytest.MonkeyPatch) -> None:
    async def point(request: Any, place: Any) -> tuple[str, float, float]:
        return "Home", 51.38, -0.25

    for mod in _TOOL_MODULES:
        if hasattr(mod, "_point"):
            monkeypatch.setattr(mod, "_point", point)


PRICED = {
    "priced_at": "2026-11-05T06:10:00+00:00", "retailers": ["sainsburys", "ocado"],
    "totals": {"by_retailer": {
        "sainsburys": {"label": "Sainsbury's", "total": 42.1, "items": 20, "missing": 0, "like_for_like": 40.0},
        "ocado": {"label": "Ocado", "total": 39.5, "items": 19, "missing": 1, "like_for_like": 39.5}},
        "compared_items": 19, "cheaper": "ocado", "saving": 0.5},
}  # fmt: skip


def test_price_text_says_each_total_and_the_cheaper_shop() -> None:
    assert M.price_text(PRICED) == ("Sainsbury's £42.10 for 20 item(s); Ocado £39.50 for 19 item(s), 1 not found. "
                                    "Ocado is cheaper by £0.50 on the 19 item(s) both have.")  # fmt: skip


async def test_shopping_reads_the_list_or_the_last_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    async def items(request: Any) -> list[dict[str, Any]]:
        return [{"key": "ko:1", "name": "Milk", "description": "2"}, {"key": "ko:2", "name": "Eggs", "description": ""}]

    async def get(request: Any, key: str) -> Any:
        return PRICED

    monkeypatch.setattr(S, "current_items", items)
    monkeypatch.setattr(S, "_get", get)
    assert (await M.shopping_tool(REQ, {}))["text"] == "2 item(s) on the shopping list: Milk (2), Eggs."
    assert (await M.shopping_tool(REQ, {"about": "prices"}))["text"].startswith("Priced at 06:10 UTC on 2026-11-05: Sainsbury's £42.10")


async def test_add_to_list_takes_several_and_says_what_was_already_there(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    async def add(request: Any, name: str, description: str = "") -> dict[str, Any]:
        seen.append((name, description))
        return {"added": name != "eggs", "already": name == "eggs", "item": {"name": name.capitalize()}}

    monkeypatch.setattr(S, "add_item", add)
    out = await M.add_to_list_tool(REQ, {"item": "milk, eggs and bread"})
    assert seen == [("milk", ""), ("eggs", ""), ("bread", "")]
    assert out["text"] == "Added Milk, Bread to the shopping list. Eggs is already on it."
    await M.add_to_list_tool(REQ, {"item": "milk", "note": "2"})
    assert seen[-1] == ("milk", "2")  # a note goes with a single item only
    with pytest.raises(ToolError):
        await M.add_to_list_tool(REQ, {"item": " "})


async def test_send_to_basket_says_what_happened_and_that_nothing_is_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    async def send(retailer: str, request: Any) -> dict[str, Any]:
        assert retailer == "ocado"
        return {"results": [{"status": "added", "item": "Milk"}, {"status": "raised", "item": "Eggs"},
                            {"status": "already", "item": "Tea"}, {"status": "failed", "item": "Saffron", "error": "gone"}],
                "basket": {"count": 9, "total": 21.4}}  # fmt: skip

    monkeypatch.setattr(S, "send_to_basket", send)
    out = await M.send_to_basket_tool(REQ, {"retailer": "Ocado"})
    assert out["text"] == ("Ocado basket: 1 added, 1 topped up, 1 already there, 1 failed. Not added: Saffron. "
                           "The Ocado basket now has 9 items, £21.40. Nothing is ordered: check out on their site or app.")  # fmt: skip
    for spoken in ("Sainsbury's", "sainsbury", "SAINSBURYS"):
        assert M._retailer({"retailer": spoken}) == "sainsburys"
    with pytest.raises(ToolError):
        M._retailer({"retailer": "tesco"})


async def test_floods_roads_buses_stops_and_postcodes_read_well(monkeypatch: pytest.MonkeyPatch, at_home: None) -> None:
    import app.modules.bods.bods
    import app.modules.codepoint.postcodes
    import app.modules.floods.floods
    import app.modules.naptan.stops
    import app.modules.tflroads.tflroads

    async def floods(request: Any, **kw: Any) -> dict[str, Any]:
        return {"count": 1, "warnings": [{"severity": "Flood alert", "area": "River Hogsmill"}]}

    async def roads(request: Any, **kw: Any) -> dict[str, Any]:
        assert kw["radius_m"] == 500
        return {"count": 1, "disruptions": [{"sub_category": "Works", "category": "Works", "closure": True,
                                             "location": "A3 Kingston Road", "metres": 230}]}  # fmt: skip

    async def buses(request: Any, **kw: Any) -> dict[str, Any]:
        return {"count": 1, "buses": [{"line": "213", "destination": "Sutton", "km": 0.4}]}

    async def stops(request: Any, **kw: Any) -> dict[str, Any]:
        return {"stops": [{"name": "Worcester Park", "indicator": "Stop G", "km": 0.12}]}

    async def pc_near(request: Any, **kw: Any) -> dict[str, Any]:
        return {"postcodes": [{"postcode": "KT4 8AA"}]}

    monkeypatch.setattr(app.modules.floods.floods, "at", floods)
    monkeypatch.setattr(app.modules.tflroads.tflroads, "at", roads)
    monkeypatch.setattr(app.modules.bods.bods, "live", buses)
    monkeypatch.setattr(app.modules.naptan.stops, "near", stops)
    monkeypatch.setattr(app.modules.codepoint.postcodes, "near", pc_near)
    assert (await M.floods_tool(REQ, {}))["text"] == "1 flood warning(s) cover Home: Flood alert for River Hogsmill."
    assert "Works (closure) at A3 Kingston Road, 230 m away" in (await M.road_disruptions_tool(REQ, {}))["text"]
    assert (await M.live_buses_tool(REQ, {}))["text"] == "1 bus(es) near Home now: 213 to Sutton, 0.2 miles away."
    assert (await M.stops_tool(REQ, {}))["text"] == "Nearest stops to Home: Worcester Park (Stop G), 120 m."
    assert (await M.postcode_tool(REQ, {}))["text"] == "The nearest postcode to Home is KT4 8AA."


async def test_add_place_needs_somewhere(at_home: None) -> None:
    with pytest.raises(ToolError, match="postcode"):
        await M.add_place_tool(REQ, {"name": "Mum's"})
    assert M._slug("Mum's house!") == "mum-s-house"


@pytest.fixture
def kitchenowl(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    monkeypatch.setenv("WA_KITCHENOWL_URL", "http://kitchenowl:8080")
    monkeypatch.setattr(S, "read_secret", lambda name, default="": "ko-token" if name == "kitchenowl_token" else default)
    get_settings.cache_clear()

    async def settings(request: Any) -> dict[str, Any]:
        return {"household_id": 1, "list_id": 7, "retailers": ["ocado"]}

    monkeypatch.setattr(S, "load_settings", settings)
    yield []
    get_settings.cache_clear()


async def test_add_item_reuses_the_list_and_posts_new_ones_by_name(kitchenowl: list[httpx.Request]) -> None:
    def answer(req: httpx.Request) -> httpx.Response:
        kitchenowl.append(req)
        if req.method == "GET":
            return httpx.Response(200, json=[{"id": 7, "items": [{"id": 11, "name": "Milk", "description": ""}]}])
        return httpx.Response(200, json={"id": 12, "name": "Bread"})

    t = httpx.MockTransport(answer)
    already = await S.add_item(REQ, "milk", transport=t)
    assert already["already"] and not already["added"]
    assert [r.method for r in kitchenowl] == ["GET"]  # nothing written for what is there
    new = await S.add_item(REQ, "  Bread ", "wholemeal", transport=t)
    assert new == {"added": True, "already": False, "item": {"key": "ko:12", "name": "Bread", "description": "wholemeal"}}
    post = kitchenowl[-1]
    assert post.url.path == "/api/shoppinglist/7/add-item-by-name" and post.headers["authorization"] == "Bearer ko-token"
    assert json.loads(post.content) == {"name": "Bread", "description": "wholemeal"}


async def test_tick_off_takes_the_newest_orders_items_off_and_clear_needs_confirming(monkeypatch: pytest.MonkeyPatch) -> None:
    async def orders(retailer: str, request: Any) -> dict[str, Any]:
        return {"orders": [
            {"id": "2", "status": "PLACED", "total": 61, "slot_start": "2026-11-06T18:00:00Z",
             "on_list": [{"key": "ko:1", "name": "Milk", "how": "product"}, {"key": "ko:2", "name": "Eggs", "how": "name"}]},
            {"id": "1", "status": "DELIVERED", "total": 50, "slot_start": "2026-10-28T09:00:00Z", "on_list": []}]}  # fmt: skip

    ticked: list[list[str]] = []

    async def tick(body: Any, request: Any) -> dict[str, Any]:
        ticked.append(body.item_keys)
        return {"removed": len(body.item_keys), "items": ["Milk", "Eggs"][: len(body.item_keys)]}

    async def clear(body: Any, request: Any) -> dict[str, Any]:
        return {"removed": 7, "items": []}

    monkeypatch.setattr(S, "orders", orders)
    monkeypatch.setattr(S, "tick_off", tick)
    monkeypatch.setattr(S, "clear_list", clear)
    said = (await M.orders_tool(REQ, {"retailer": "sainsburys"}))["text"]
    assert said.startswith("Recent Sainsbury's orders: 2026-11-06, placed, £61.00, covers 2 item(s) on the list (Milk, Eggs)")
    out = await M.tick_off_order_tool(REQ, {"retailer": "sainsburys"})
    assert ticked == [["ko:1", "ko:2"]] and out["text"].startswith("Took 2 item(s) off the list, from the Sainsbury's order")
    await M.tick_off_order_tool(REQ, {"retailer": "sainsburys", "include_name_matches": False})
    assert ticked[-1] == ["ko:1"]
    nothing = await M.tick_off_order_tool(REQ, {"retailer": "sainsburys", "order": "1"})
    assert nothing["text"].startswith("Nothing on the list is in the Sainsbury's order")
    with pytest.raises(ToolError, match="confirm"):
        await M.clear_list_tool(REQ, {})
    assert "7 item(s) taken off" in (await M.clear_list_tool(REQ, {"confirm": True}))["text"]


STOPS_406 = {
    "outbound": {"towards": "Epsom, Clock Tower", "stops": [
        {"id": "K1", "name": "Kingston Cromwell Road Bus Station", "pos": 0},
        {"id": "T1", "name": "Tolworth Broadway", "pos": 1},
        {"id": "E1", "name": "Epsom Station", "pos": 2},
        {"id": "E2", "name": "Epsom Clock Tower", "pos": 3}]},
    "inbound": {"towards": "Kingston Cromwell Road Bus Station", "stops": [
        {"id": "E3", "name": "Epsom Clock Tower", "pos": 0},
        {"id": "E4", "name": "Epsom Station", "pos": 1},
        {"id": "T2", "name": "Tolworth Broadway", "pos": 2},
        {"id": "K2", "name": "Kingston Cromwell Road Bus Station", "pos": 3}]},
    "_source": "tfl", "_operator": None,
}  # fmt: skip


@contextlib.asynccontextmanager
async def _conn() -> Any:
    yield None


def _bus_mocks(monkeypatch: pytest.MonkeyPatch, existing: list[str]) -> dict[str, Any]:
    import types

    import app.modules.bus.bus as B
    from app.modules.bus.sources.bus import store

    got: dict[str, Any] = {}

    async def line_stops(request: Any, line: str, **kw: Any) -> dict[str, Any]:
        return STOPS_406

    async def setup(request: Any, body: Any) -> dict[str, Any]:
        got["body"] = body
        out: dict[str, Any] = {"id": body.name, "stops_tracked": []}
        if body.return_earliest:
            out["return_tracker"] = {"id": f"{body.name}-return", "board": "Kingston Cromwell Road Bus Station",
                                     "target": "Epsom Station", "earliest": body.return_earliest,
                                     "deadline": body.return_deadline}  # fmt: skip
        return out

    async def list_trackers(conn: Any) -> list[dict[str, Any]]:
        return [{"id": i} for i in existing]

    monkeypatch.setattr(B, "line_stops", line_stops)
    monkeypatch.setattr(B, "setup", setup)
    monkeypatch.setattr(store, "list_trackers", list_trackers)
    got["request"] = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(
        engine=types.SimpleNamespace(connect=_conn))))  # fmt: skip
    return got


async def test_bus_setup_finds_the_direction_from_loose_stop_names(monkeypatch: pytest.MonkeyPatch) -> None:
    got = _bus_mocks(monkeypatch, ["main"])
    r = await M.bus_setup_tool(got["request"], {"line": "406", "board": "epsom station", "to": "Kingston",
                                                "arrive_by": "8.30", "return_earliest": "17:00", "return_arrive_by": "1830"})  # fmt: skip
    b = got["body"]
    assert (b.board, b.target, b.earliest, b.deadline, b.name, b.source) == ("E4", "K2", "07:30", "08:30", "bus-406", "tfl")
    assert (b.return_earliest, b.return_deadline) == ("17:00", "18:30")
    assert r["text"].startswith("Tracking the 406 from Epsom Station to Kingston Cromwell Road Bus Station "
                                "(towards Kingston Cromwell Road Bus Station), buses from 07:30, to arrive by 08:30")  # fmt: skip
    assert "The way back" in r["text"]
    await M.bus_setup_tool(got["request"], {"line": "406", "board": "Tolworth", "to": "Epsom clock tower", "arrive_by": "09:00"})
    assert (got["body"].board, got["body"].target) == ("T1", "E2")  # the other way


async def test_bus_setup_lists_stops_and_says_what_is_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    got = _bus_mocks(monkeypatch, ["bus-406"])
    r = await M.bus_setup_tool(got["request"], {"line": "406"})
    assert "Towards Epsom, Clock Tower: Kingston Cromwell Road Bus Station, Tolworth Broadway" in r["text"]
    r = await M.bus_setup_tool(got["request"], {"line": "406", "board": "Surbiton", "to": "Epsom", "arrive_by": "08:00"})
    assert r["text"].startswith("No stop on this line matches 'Surbiton'.") and "body" not in got
    with pytest.raises(ToolError, match="arrive by"):
        await M.bus_setup_tool(got["request"], {"line": "406", "board": "Epsom Station", "to": "Kingston"})
    with pytest.raises(ToolError, match="already exists"):  # never replaces one without being told
        await M.bus_setup_tool(got["request"], {"line": "406", "board": "Epsom Station", "to": "Kingston", "arrive_by": "08:00"})
    await M.bus_setup_tool(got["request"], {"line": "406", "board": "Epsom Station", "to": "Kingston", "arrive_by": "08:00",
                                            "replace": True})  # fmt: skip
    assert got["body"].name == "bus-406"
    with pytest.raises(ToolError, match="time like"):
        await M.bus_setup_tool(got["request"], {"line": "406", "board": "Epsom Station", "to": "Kingston", "arrive_by": "soon"})

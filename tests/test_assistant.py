from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import assistant
from app.main import app

H = {"Authorization": "Bearer tok"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_TOKEN", "tok")
    return TestClient(app)


def rpc(
    client: TestClient, method: str, params: dict[str, Any] | None = None, rid: int | None = 1
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        body["id"] = rid
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=H)


def test_mcp_requires_the_token(client: TestClient) -> None:
    assert client.post("/mcp", json={}).status_code in (401, 503)
    assert (
        client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    )
    assert client.post("/v1/ask/list_options").status_code in (401, 503)


def test_initialize_list_ping_and_notification(client: TestClient) -> None:
    r = rpc(client, "initialize", {"protocolVersion": "2025-03-26"}).json()["result"]
    assert r["protocolVersion"] == "2025-03-26" and "tools" in r["capabilities"]
    assert (
        rpc(client, "initialize", {"protocolVersion": "1999"}).json()["result"]["protocolVersion"]
        == "2025-06-18"
    )
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} >= {
        "list_options",
        "fuel_cheapest",
        "trip_cost",
        "trains",
        "bus",
    }
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    assert rpc(client, "ping").json()["result"] == {}
    assert rpc(client, "notifications/initialized", rid=None).status_code == 202
    assert rpc(client, "nope").json()["error"]["code"] == -32601
    assert client.post("/mcp", content=b"{", headers=H).json()["error"]["code"] == -32700
    assert client.post("/mcp", json={"hello": 1}, headers=H).json()["error"]["code"] == -32600
    assert client.get("/mcp", headers=H).status_code == 405


def test_batch(client: TestClient) -> None:
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    out = client.post("/mcp", json=batch, headers=H).json()
    assert len(out) == 1 and out[0]["id"] == 1


def test_tool_call_success_error_and_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok(_r: Any, args: dict[str, Any]) -> dict[str, Any]:
        return {"text": f"hello {args.get('who')}", "data": {"x": 1}}

    async def bad(_r: Any, _a: dict[str, Any]) -> dict[str, Any]:
        raise assistant.ToolError("no such thing")

    async def boom(_r: Any, _a: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("secret internals")

    for name, fn in (("ok", ok), ("bad", bad), ("boom", boom)):
        monkeypatch.setitem(assistant.TOOLS, name, assistant.Tool(name, "", {"type": "object"}, fn))
    r = rpc(client, "tools/call", {"name": "ok", "arguments": {"who": "you"}}).json()["result"]
    assert (
        r["content"][0]["text"] == "hello you"
        and r["structuredContent"] == {"x": 1}
        and not r["isError"]
    )
    r = rpc(client, "tools/call", {"name": "bad", "arguments": {}}).json()["result"]
    assert r["isError"] and r["content"][0]["text"] == "no such thing"
    r = rpc(client, "tools/call", {"name": "boom", "arguments": {}}).json()["result"]
    assert r["isError"] and "secret internals" not in r["content"][0]["text"]
    assert rpc(client, "tools/call", {"name": "missing"}).json()["error"]["code"] == -32602
    # the same tools over plain HTTP
    assert (
        client.post("/v1/ask/ok?format=text", json={"who": "Siri"}, headers=H).text == "hello Siri"
    )
    assert client.post("/v1/ask/ok", json={"who": "a"}, headers=H).json()["data"] == {"x": 1}
    assert client.post("/v1/ask/bad", headers=H).status_code == 422
    assert client.post("/v1/ask/bad?format=text", headers=H).text == "no such thing"
    assert client.post("/v1/ask/zzz", headers=H).status_code == 422
    assert {t["tool"] for t in client.get("/v1/ask", headers=H).json()} >= {"fuel_cheapest"}


async def test_fuel_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def near(_r: Any, **kw: Any) -> dict[str, Any]:
        assert kw["place"] == "home" and kw["fuel"] == "E10" and kw["limit"] == 3
        return {"place": "Home", "stations": [
            {"brand": "Asda", "name": "x", "city": "Epsom", "pence": 129.9, "distance_km": 3.2},
            {"brand": None, "name": "Joe's Garage", "city": None, "pence": 133.9, "distance_km": 1.6},
        ]}  # fmt: skip

    monkeypatch.setattr(assistant, "fuel_near", near)
    out = await assistant.run_tool(None, "fuel_cheapest", {"place": "home"})  # type: ignore[arg-type]
    assert out["text"] == ("E10 near Home: cheapest is Asda, Epsom, 129.9 pence, 2.0 miles away; "
                           "Joe's Garage, 133.9 pence, 1.0 mile away.")  # fmt: skip

    async def near_default(_r: Any, **kw: Any) -> dict[str, Any]:
        assert kw["place"] is None  # no place given: the endpoint chooses home
        return {"place": "Home", "stations": []}

    monkeypatch.setattr(assistant, "fuel_near", near_default)
    out = await assistant.run_tool(None, "fuel_cheapest", {})  # type: ignore[arg-type]
    assert "No E10 prices found" in out["text"] and "Home" in out["text"]


async def test_fuel_http_error_becomes_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def near(_r: Any, **_kw: Any) -> dict[str, Any]:
        raise HTTPException(404, "no place 'x'")

    monkeypatch.setattr(assistant, "fuel_near", near)
    with pytest.raises(assistant.ToolError, match="no place"):
        await assistant.run_tool(None, "fuel_cheapest", {"place": "x"})  # type: ignore[arg-type]


async def test_trains_and_trip_cost_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def deps(_board: str, _r: Any) -> dict[str, Any]:
        return {"board": "To Waterloo", "departures": [
            {"destination": "London Waterloo", "minutes": 6, "platform": "1", "status": "OnTime", "delay_minutes": 0},
            {"destination": "London Waterloo", "minutes": 21, "platform": None, "status": "Delayed", "delay_minutes": 4},
            {"destination": "London Waterloo", "minutes": 36, "platform": None, "status": "Cancelled"},
        ]}  # fmt: skip

    monkeypatch.setattr(assistant, "board_departures", deps)
    t = (await assistant.run_tool(None, "trains", {"board": "b"}))["text"]  # type: ignore[arg-type]
    assert t == ("To Waterloo: London Waterloo in 6 minutes, platform 1; "
                 "London Waterloo in 21 minutes, 4 minutes late; London Waterloo in 36 minutes, cancelled.")  # fmt: skip

    async def cost(trip: str, _r: Any, vehicle: str, _w: float) -> dict[str, Any]:
        if vehicle == "leaf":
            return {"trip": "Silverstone", "vehicle": "Leaf", "kind": "ev", "distance_km": 160.9, "kwh": 31.0,
                    "one_way_on_a_full_charge": True, "return_on_a_full_charge": False,
                    "cost_gbp_at_home_rate": 8.5}  # fmt: skip
        return {"trip": "Silverstone", "vehicle": "Estima", "distance_km": 160.9, "litres": 22.4,
                "cost_gbp_typical": 29.1, "cost_gbp_cheapest": 27.8}  # fmt: skip

    monkeypatch.setattr(assistant, "trip_cost", cost)
    ev = (await assistant.run_tool(None, "trip_cost", {"trip": "s", "vehicle": "leaf"}))["text"]  # type: ignore[arg-type]
    assert (
        "100 miles" in ev
        and "makes it on one charge" in ev
        and "return needs a charge" in ev
        and "8.50" in ev
    )
    ice = (await assistant.run_tool(None, "trip_cost", {"trip": "s", "vehicle": "estima"}))["text"]  # type: ignore[arg-type]
    assert "22 litres" in ice and "29.10" in ice and "27.80" in ice

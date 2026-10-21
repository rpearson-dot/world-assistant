from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.integrations.fuelfinder.client import (
    PRICES,
    STATIONS,
    AuthError,
    FuelFinderClient,
    IncompleteError,
)
from app.integrations.fuelfinder.normalise import get_path, parse_timestamp, to_bool, to_pence


async def _nosleep(_s: float) -> None:
    return None


def make_client(handler: httpx.MockTransport | None = None, **kw) -> FuelFinderClient:
    return FuelFinderClient("id", "secret", transport=handler, sleep=_nosleep, **kw)


class Server:
    """A fake Fuel Finder: scripted responses per (path, batch)."""

    def __init__(self, script: dict[tuple[str, int], list[httpx.Response]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.token_calls = 0
        self.token_bodies: list[str] = []
        self.requests: list[tuple[str, int]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/generate_access_token"):
            self.token_calls += 1
            self.token_bodies.append(request.content.decode())
            return httpx.Response(200, json={"data": {"access_token": f"tok{self.token_calls}"}})
        assert request.headers["authorization"].startswith("Bearer tok")
        key = (request.url.path.removeprefix("/api/v1"), int(request.url.params["batch-number"]))
        self.requests.append(key)
        queue = self.script.get(key)
        if not queue:
            return httpx.Response(404)
        return queue.pop(0) if len(queue) > 1 else queue[0]


def ok(n: int) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": i} for i in range(n)]})


async def test_pulls_all_batches_until_404() -> None:
    server = Server({(STATIONS, 1): [ok(500)], (STATIONS, 2): [ok(3)]})
    async with make_client(httpx.MockTransport(server), base_url="https://x.test/api/v1") as c:
        records = await c.fetch_all(STATIONS)
    assert len(records) == 503
    assert server.requests == [(STATIONS, 1), (STATIONS, 2), (STATIONS, 3)]
    assert server.token_calls == 1
    assert "grant_type=client_credentials" in server.token_bodies[0]
    assert "scope=fuelfinder.read" in server.token_bodies[0]


async def test_empty_200_is_retried_not_treated_as_the_end() -> None:
    empty = httpx.Response(200, json={"data": []})
    server = Server({(PRICES, 1): [empty, empty, ok(5)]})
    async with make_client(httpx.MockTransport(server), base_url="https://x.test/api/v1") as c:
        records = await c.fetch_all(PRICES)
    assert len(records) == 5


async def test_persistent_empty_200_raises_instead_of_truncating() -> None:
    empty = httpx.Response(200, json={"data": []})
    server = Server({(PRICES, 1): [empty]})
    async with make_client(httpx.MockTransport(server), base_url="https://x.test/api/v1") as c:
        with pytest.raises(IncompleteError):
            await c.fetch_all(PRICES)


async def test_server_errors_are_retried() -> None:
    server = Server({(STATIONS, 1): [httpx.Response(503), httpx.Response(500), ok(2)]})
    async with make_client(httpx.MockTransport(server), base_url="https://x.test/api/v1") as c:
        assert len(await c.fetch_all(STATIONS)) == 2


async def test_expired_token_is_refreshed_once() -> None:
    server = Server({(STATIONS, 1): [httpx.Response(401), ok(1)]})
    async with make_client(httpx.MockTransport(server), base_url="https://x.test/api/v1") as c:
        assert len(await c.fetch_all(STATIONS)) == 1
    assert server.token_calls == 2


async def test_bad_credentials_are_not_retried() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "invalid_client"})

    async with make_client(httpx.MockTransport(handler), base_url="https://x.test/api/v1") as c:
        with pytest.raises(AuthError):
            await c.fetch_all(STATIONS)
    assert calls["n"] == 1  # one token attempt, no retry, no data request


async def test_token_without_data_nesting_is_an_auth_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "top-level"})

    async with make_client(httpx.MockTransport(handler), base_url="https://x.test/api/v1") as c:
        with pytest.raises(AuthError):
            await c.fetch_all(STATIONS)


def test_to_pence_handles_pounds_pence_and_nonsense() -> None:
    assert to_pence(138.9) == 138.9
    assert to_pence("1.389") == 138.9
    assert to_pence(0) is None
    assert to_pence(5000) is None
    assert to_pence(12) is None  # implausibly cheap
    assert to_pence("n/a") is None
    assert to_pence(None) is None


def test_parse_timestamp_js_and_iso() -> None:
    js = parse_timestamp("Sat Mar 07 2026 15:34:42 GMT+0000 (Coordinated Universal Time)")
    assert js == datetime(2026, 3, 7, 15, 34, 42, tzinfo=UTC)
    bst = parse_timestamp("Sun Oct 18 2026 12:00:00 GMT+0100 (British Summer Time)")
    assert bst == datetime(2026, 10, 18, 11, 0, 0, tzinfo=UTC)
    assert parse_timestamp("2025-09-05 14:30:00") == datetime(2025, 9, 5, 14, 30, tzinfo=UTC)
    assert parse_timestamp("") is None
    assert parse_timestamp("garbage") is None
    assert parse_timestamp(None) is None


def test_to_bool_and_get_path() -> None:
    assert to_bool("TRUE") is True
    assert to_bool("FALSE") is False
    assert to_bool("") is None
    nested = {"location": {"latitude": 51.5}}
    flat = {"forecourts.location.latitude": 51.5}
    short = {"location.latitude": 51.5}
    for rec in (nested, flat, short):
        assert get_path(rec, "location.latitude") == 51.5
    assert get_path({}, "location.latitude") is None


def test_describe_body_is_short_and_secret_free() -> None:
    from app.integrations.fuelfinder.client import describe_body

    assert describe_body({"data": [], "meta": {"a": 1}}) == "keys data:list, meta:dict"
    assert describe_body([1, 2, 3]) == "list of 3"
    assert len(describe_body("x" * 500)) <= 82


async def test_bare_json_list_response_is_accepted() -> None:
    """Confirmed live: /pfs returns a top-level list rather than {"data": [...]}."""
    bare = httpx.Response(200, json=[{"node_id": "a"}, {"node_id": "b"}])
    server = Server({(STATIONS, 1): [bare]})
    async with make_client(httpx.MockTransport(server), base_url="https://x.test/api/v1") as c:
        records = await c.fetch_all(STATIONS)
    assert [r["node_id"] for r in records] == ["a", "b"]

from datetime import date
from typing import Any, Self

import httpx
import pytest

from app import assistant
from app.integrations.skiddle.client import SkiddleClient, SkiddleError, parse_event

RAW = {
    "id": 123, "eventname": "Live at the Hall", "date": "2026-10-24", "EventCode": "LIVE",
    "entryprice": "£12.50", "minage": "18", "link": "https://www.skiddle.com/e/123",
    "imageurl": "https://img/x.jpg", "description": "A night out",
    "venue": {"name": "The Hall", "town": "Kingston", "postcode": "KT1 1AA",
              "latitude": "51.5859", "longitude": "-0.1278"},
    "openingtimes": {"doorsopen": "19:00"},
}  # fmt: skip


def client(handler: Any, **kw: Any) -> SkiddleClient:
    async def nosleep(_: float) -> None:
        return None

    return SkiddleClient("KEY123", transport=httpx.MockTransport(handler), sleep=nosleep, **kw)


def test_parse_event_is_flat_and_computes_distance() -> None:
    e = parse_event(RAW, (51.5074, -0.1278))
    assert e["name"] == "Live at the Hall" and e["type"] == "LIVE" and e["venue"] == "The Hall"
    assert e["doors_open"] == "19:00" and e["price"] == "£12.50" and 7 < e["distance_km"] < 10
    assert e["url"] == "https://www.skiddle.com/e/123"


def test_parse_event_survives_missing_and_odd_fields() -> None:
    e = parse_event({"id": 1, "venue": "not a dict", "link": "javascript:alert(1)", "date": None})
    assert e["name"] is None and e["venue"] is None and e["url"] is None and e["distance_km"] is None


async def test_search_sends_all_geo_params_together_and_the_key() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        assert req.url.path == "/api/v1/events/search/"
        return httpx.Response(200, json={"results": [RAW], "totalcount": 1})

    async with client(handler) as c:
        res = await c.search(
            51.5074, -0.1278, radius_km=8, min_date=date(2026, 10, 21), max_date=date(2026, 10, 28),
            event_code="live", keyword=" jazz ", limit=500,
        )  # fmt: skip
    assert seen["latitude"] == "51.50740" and seen["longitude"] == "-0.12780" and seen["radius"] == "5"
    assert seen["minDate"] == "2026-10-21" and seen["maxDate"] == "2026-10-28"
    assert seen["eventcode"] == "LIVE" and seen["keyword"] == "jazz" and seen["limit"] == "100"
    assert seen["api_key"] == "KEY123"
    assert res["total"] == 1 and res["events"][0]["name"] == "Live at the Hall"


async def test_unknown_event_type_is_refused_before_any_request() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with client(handler) as c:
        with pytest.raises(SkiddleError, match="unknown event type"):
            await c.search(51.0, 0.0, event_code="PARTY")


async def test_missing_key_is_a_clear_error() -> None:
    async with SkiddleClient("") as c:
        with pytest.raises(SkiddleError, match="skiddle_api_key"):
            await c.search(51.0, 0.0)


async def test_errors_never_contain_the_key() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed for " + str(req.url))

    async with client(boom, max_attempts=2) as c:
        with pytest.raises(SkiddleError) as exc:
            await c.search(51.0, 0.0)
    assert "KEY123" not in str(exc.value) and "ConnectError" in str(exc.value)

    async with client(lambda r: httpx.Response(403, text="KEY123 bad")) as c:
        with pytest.raises(SkiddleError) as exc2:
            await c.search(51.0, 0.0)
    assert "KEY123" not in str(exc2.value) and "check the key" in str(exc2.value)


async def test_retries_server_errors_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"results": []})

    async with client(handler) as c:
        res = await c.search(51.0, 0.0)
    assert calls["n"] == 3 and res["events"] == []


async def test_error_in_a_200_body_is_reported() -> None:
    async with client(lambda r: httpx.Response(200, json={"error": "bad radius"})) as c:
        with pytest.raises(SkiddleError, match="bad radius"):
            await c.search(51.0, 0.0)


async def test_assistant_events_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def near(request: Any, **kw: Any) -> dict[str, Any]:
        assert kw["place"] == "home" and kw["days"] == 7 and kw["type"] == "COMEDY"
        return {"place": "Home", "total": 7, "events": [
            {"name": "Stand up", "venue": "The Pub", "date": "2026-10-24"},
            {"name": "Late show", "venue": None, "date": None}]}  # fmt: skip

    monkeypatch.setattr(assistant, "events_near", near)
    out = await assistant.run_tool(None, "events_near", {"place": "home", "type": "comedy"})  # type: ignore[arg-type]
    assert "7 events near Home" in out["text"] and "Stand up at The Pub on 2026-10-24" in out["text"]
    assert "and 5 more" in out["text"]


class _FakeRedis:
    def __init__(self) -> None:
        self.d: dict[str, str] = {}

    async def get(self, k: str) -> str | None:
        return self.d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None) -> None:
        self.d[k] = v


def test_endpoint_caches_and_needs_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app import events
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.setenv("SKIDDLE_API_KEY", "k")
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    calls = {"n": 0}

    class Fake:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def search(self, lat: float, lon: float, **kw: Any) -> dict[str, Any]:
            calls["n"] += 1
            assert kw["event_code"] == "LIVE" and kw["max_date"] > kw["min_date"]
            return {"total": 1, "events": [parse_event(RAW, (lat, lon))], "raw_keys": []}

    async def fake_place(request: Any, place: str) -> tuple[str, float, float]:
        return "Home", 51.5074, -0.1278

    monkeypatch.setattr(events, "_client", lambda request: Fake())
    monkeypatch.setattr(events, "_place", fake_place)
    app.state.redis = _FakeRedis()
    c = TestClient(app)
    assert c.get("/v1/events/near?place=home").status_code == 401
    h = {"Authorization": "Bearer s3cret"}
    a = c.get("/v1/events/near?place=home&type=LIVE", headers=h).json()
    b = c.get("/v1/events/near?place=home&type=LIVE", headers=h).json()
    assert a["cached"] is False and b["cached"] is True and calls["n"] == 1
    assert a["events"][0]["name"] == "Live at the Hall" and a["sources"] == ["skiddle"]


REAL = {
    "id": "42573468", "EventCode": "EXPER", "eventname": "Chessington 1 Day Entry", "cancelled": "0",
    "rescheduledDate": "", "date": "2026-10-22", "startdate": "2026-10-22T00:00:00+00:00",
    "openingtimes": {"doorsopen": "00:00"}, "tickets": True, "hotSeller": False, "currency": "GBP",
    "entryprice": "\u00a332.00 - \u00a342.00", "link": "https://www.skiddle.com/x/42573468/",
    "venue": {"name": "Chessington", "town": "Chessington", "latitude": 51.5389, "longitude": -0.1278},
}  # fmt: skip


def test_real_response_shape() -> None:
    e = parse_event(REAL, (51.5074, -0.1278))
    assert e["type"] == "EXPER" and e["doors_open"] is None and e["availability"] == "Tickets on sale"
    assert e["price"] == "£32.00 - £42.00" and 3 < e["distance_km"] < 4


def test_price_without_a_symbol_gets_the_pound_sign() -> None:
    assert parse_event({"entryprice": "18.00", "currency": "GBP"})["price"] == "£18.00"
    assert parse_event({"entryprice": "Free", "currency": "GBP"})["price"] == "Free"


def test_times_cancellations_and_selling_fast() -> None:
    assert parse_event({"startdate": "2026-10-24T19:30:00+00:00"})["doors_open"] == "19:30"
    assert parse_event({"openingtimes": {"doorsopen": "20:00"}})["doors_open"] == "20:00"
    assert parse_event({"cancelled": "1", "tickets": True})["availability"] == "Cancelled"
    assert parse_event({"rescheduledDate": "2026-11-02"})["availability"] == "Rescheduled to 2026-11-02"
    assert parse_event({"hotSeller": True, "tickets": True})["availability"] == "Selling fast"
    assert parse_event({"tickets": False})["availability"] is None


async def test_string_totalcount_and_zero_error_are_fine() -> None:
    body = {"error": 0, "totalcount": "57", "results": [REAL], "pagecount": 3}
    async with client(lambda r: httpx.Response(200, json=body)) as c:
        res = await c.search(51.0, 0.0)
    assert res["total"] == 57 and len(res["events"]) == 1

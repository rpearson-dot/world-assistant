from datetime import UTC, datetime
from typing import Any, Self

import httpx
import pytest

from app.integrations.ticketmaster.client import (
    TicketmasterClient,
    TicketmasterError,
    geohash,
    parse_event,
)

RAW = {
    "id": "G5v0Z", "name": "Big Gig", "url": "https://www.ticketmaster.co.uk/event/1",
    "dates": {"start": {"localDate": "2026-10-25", "localTime": "19:30:00"}, "status": {"code": "onsale"}},
    "classifications": [{"segment": {"name": "Music"}, "genre": {"name": "Rock"}}],
    "priceRanges": [{"type": "standard", "currency": "GBP", "min": 25.0, "max": 60.0}],
    "_embedded": {"venues": [{"name": "Hall", "city": {"name": "Kingston upon Thames"},
                              "postalCode": "KT1 1AA",
                              "location": {"latitude": "51.5859", "longitude": "-0.1278"}}]},
}  # fmt: skip


def client(handler: Any) -> TicketmasterClient:
    async def nosleep(_: float) -> None:
        return None

    return TicketmasterClient("TMKEY", transport=httpx.MockTransport(handler), sleep=nosleep)


def test_geohash_known_vectors() -> None:
    assert geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"
    assert geohash(42.6, -5.6, 5) == "ezs42"


def test_parse_event_matches_the_shared_shape() -> None:
    e = parse_event(RAW, (51.5074, -0.1278))
    assert e["name"] == "Big Gig" and e["date"] == "2026-10-25" and e["doors_open"] == "19:30"
    assert e["type"] == "Music" and e["venue"] == "Hall" and e["town"] == "Kingston upon Thames"
    assert e["price"] == "£25.00 to £60.00" and e["status"] == "onsale" and 7 < e["distance_km"] < 10
    assert e["url"] == "https://www.ticketmaster.co.uk/event/1"


def test_parse_event_survives_missing_fields() -> None:
    e = parse_event({"id": "x", "url": "http://insecure", "_embedded": {"venues": []}, "dates": None})
    assert e["name"] is None and e["venue"] is None and e["url"] is None and e["price"] is None


async def test_search_params_and_parsing() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        assert req.url.path == "/discovery/v2/events.json"
        return httpx.Response(200, json={"_embedded": {"events": [RAW]}, "page": {"totalElements": 42}})

    async with client(handler) as c:
        res = await c.search(
            51.5074, -0.1278, radius_km=8, start=datetime(2026, 10, 21, tzinfo=UTC),
            end=datetime(2026, 10, 28, 23, 59, tzinfo=UTC), event_type="live", keyword="rock",
        )  # fmt: skip
    assert seen["apikey"] == "TMKEY" and seen["unit"] == "km" and seen["radius"] == "8"
    assert seen["geoPoint"] == geohash(51.5074, -0.1278) and seen["classificationName"] == "music"
    assert seen["startDateTime"] == "2026-10-21T00:00:00Z" and seen["countryCode"] == "GB"
    assert res["total"] == 42 and res["events"][0]["name"] == "Big Gig"


async def test_type_with_no_equivalent_skips_the_request() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with client(handler) as c:
        assert await c.search(51.0, 0.0, event_type="CLUB") == {"total": 0, "events": []}


async def test_no_events_is_an_empty_list_not_an_error() -> None:
    async with client(lambda r: httpx.Response(200, json={"page": {"totalElements": 0}})) as c:
        assert (await c.search(51.0, 0.0))["events"] == []


async def test_errors_never_contain_the_key() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed " + str(req.url))

    async with client(boom) as c:
        with pytest.raises(TicketmasterError) as exc:
            await c.search(51.0, 0.0)
    assert "TMKEY" not in str(exc.value)
    async with client(lambda r: httpx.Response(401, text="TMKEY")) as c:
        with pytest.raises(TicketmasterError) as exc2:
            await c.search(51.0, 0.0)
    assert "TMKEY" not in str(exc2.value) and "check the key" in str(exc2.value)


async def test_missing_key() -> None:
    async with TicketmasterClient("") as c:
        with pytest.raises(TicketmasterError, match="ticketmaster_api_key"):
            await c.search(51.0, 0.0)


# ---- merged endpoint -------------------------------------------------------------------------


class _Redis:
    d: dict[str, str]

    def __init__(self) -> None:
        self.d = {}

    async def get(self, k: str) -> str | None:
        return self.d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None) -> None:
        self.d[k] = v


class _Fake:
    def __init__(self, events: list[dict[str, Any]] | Exception) -> None:
        self.events = events

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def search(self, lat: float, lon: float, **kw: Any) -> dict[str, Any]:
        if isinstance(self.events, Exception):
            raise self.events
        return {"total": len(self.events), "events": self.events}


def _setup(monkeypatch: pytest.MonkeyPatch, sk: Any, tm: Any) -> Any:
    from fastapi.testclient import TestClient

    from app import events
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.setenv("SKIDDLE_API_KEY", "a")
    monkeypatch.setenv("TICKETMASTER_API_KEY", "b")

    async def place(request: Any, place: str) -> tuple[str, float, float]:
        return "Home", 51.51, -0.13

    monkeypatch.setattr(events, "_place", place)
    monkeypatch.setattr(events, "_client", lambda r: _Fake(sk))
    monkeypatch.setattr(events, "_tm_client", lambda r: _Fake(tm))
    app.state.redis = _Redis()
    return TestClient(app)


H = {"Authorization": "Bearer s3cret"}


def test_endpoint_merges_and_sorts_by_date(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _setup(
        monkeypatch,
        [{"name": "S late", "date": "2026-10-27", "doors_open": None}],
        [{"name": "T early", "date": "2026-10-22", "doors_open": "19:00"}],
    )
    d = c.get("/v1/events/near?place=home", headers=H).json()
    assert [e["name"] for e in d["events"]] == ["T early", "S late"]
    assert [e["source"] for e in d["events"]] == ["ticketmaster", "skiddle"] and d["errors"] == []


def test_one_source_failing_still_returns_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.ticketmaster.client import TicketmasterError

    c = _setup(monkeypatch, [{"name": "S", "date": "2026-10-27"}], TicketmasterError("down"))
    r = c.get("/v1/events/near?place=home", headers=H)
    d = r.json()
    assert r.status_code == 200 and len(d["events"]) == 1 and "ticketmaster: down" in d["errors"][0]


def test_all_sources_failing_is_a_502(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.skiddle.client import SkiddleError
    from app.integrations.ticketmaster.client import TicketmasterError

    c = _setup(monkeypatch, SkiddleError("a"), TicketmasterError("b"))
    assert c.get("/v1/events/near?place=home", headers=H).status_code == 502


def test_source_filter_and_no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _setup(monkeypatch, [{"name": "S", "date": "2026-10-27"}], [{"name": "T", "date": "2026-10-22"}])
    d = c.get("/v1/events/near?place=home&source=skiddle", headers=H).json()
    assert d["sources"] == ["skiddle"] and [e["name"] for e in d["events"]] == ["S"]
    monkeypatch.delenv("SKIDDLE_API_KEY")
    monkeypatch.delenv("TICKETMASTER_API_KEY")
    assert c.get("/v1/events/near?place=home&days=3", headers=H).status_code == 503


def test_availability_is_the_sale_status_not_seat_counts() -> None:
    from app.integrations.ticketmaster.client import _availability

    now = datetime(2026, 10, 21, tzinfo=UTC)
    assert _availability("onsale", {"public": {"startDateTime": "2026-01-01T10:00:00Z"}}, now) == "On sale"
    assert _availability("onsale", {"public": {"startDateTime": "2026-11-05T09:00:00Z"}}, now) == "On sale from 2026-11-05"
    assert _availability("offsale", None, now).startswith("Off sale")  # type: ignore[union-attr]
    assert _availability("canceled", None, now) == "Cancelled"
    assert _availability("postponed", None, now) == "Postponed"
    assert _availability(None, None, now) is None
    assert parse_event(RAW)["availability"] == "On sale"

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.integrations.rail.rdm import RailError, RdmClient, parse_services

NOW = datetime(2026, 10, 20, 6, 50, tzinfo=UTC)  # 07:50 UK time (BST)

DATA = {
    "locationName": "Stoneleigh", "crs": "SNL",
    "trainServices": [
        {"std": "07:58", "etd": "On time", "platform": "1", "operator": "South Western Railway",
         "destination": [{"locationName": "London Waterloo", "crs": "WAT"}], "isCancelled": False},
        {"std": "08:05", "etd": "08:11", "platform": None, "operator": "South Western Railway",
         "destination": [{"locationName": "London Waterloo"}], "delayReason": "A fault with the signalling"},
        {"std": "08:12", "etd": "Cancelled", "isCancelled": True, "cancelReason": "A broken down train",
         "destination": [{"locationName": "London Waterloo"}]},
        {"std": "08:20", "etd": "Delayed", "destination": [{"locationName": "Epsom"}, {"locationName": "Leatherhead"}]},
        "junk",
    ],
}  # fmt: skip


def test_parse_services_statuses_and_times() -> None:
    deps = parse_services(DATA, NOW)
    assert len(deps) == 4
    a, b, c, d = deps
    assert a.status == "OnTime" and a.platform == "1" and a.destination == "London Waterloo"
    assert a.scheduled == datetime(2026, 10, 20, 6, 58, tzinfo=UTC) and a.delay_minutes == 0
    assert (
        b.status == "Delayed" and b.delay_minutes == 6 and b.cause == "A fault with the signalling"
    )
    assert b.platform is None
    assert c.status == "Cancelled" and c.cause == "A broken down train"
    assert (
        d.status == "Delayed"
        and d.destination == "Epsom and Leatherhead"
        and d.expected == d.scheduled
    )


def test_times_wrap_past_midnight() -> None:
    late = datetime(2026, 10, 20, 22, 50, tzinfo=UTC)  # 23:50 UK
    deps = parse_services({"trainServices": [{"std": "00:10", "etd": "On time"}]}, late)
    assert deps[0].scheduled == datetime(2026, 10, 20, 23, 10, tzinfo=UTC)  # 00:10 BST the next day


async def test_client_retries_then_gives_up() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    async def nosleep(_: float) -> None: ...

    async with RdmClient(
        "https://x.example", transport=httpx.MockTransport(handler), sleep=nosleep
    ) as c:
        with pytest.raises(RailError):
            await c.departures("SNL")
    assert calls == 3


def test_search_response_shape_is_tolerated() -> None:
    assert isinstance(parse_services({}, NOW), list)
    assert parse_services({"trainServices": None}, NOW) == []
    _: Any = None


async def test_rdm_client_header_path_and_params() -> None:
    from app.integrations.rail.rdm import DEFAULT_BASE

    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"locationName": "Stoneleigh", "trainServices": []})

    async with RdmClient(DEFAULT_BASE, "KEY123", transport=httpx.MockTransport(handler)) as c:
        await c.departures("snl", 5, "wat")
        await c.departures("SNL")
        with pytest.raises(RailError):
            await c.departures("S;N")
        with pytest.raises(RailError):
            await c.search_stations("stoneleigh")  # no search on the marketplace
        assert (await c.search_stations("snl"))[0]["crsCode"] == "SNL"
    first = seen[0]
    assert (
        first.url.path
        == "/1010-live-departure-board-dep1_2/LDBWS/api/20220120/GetDepBoardWithDetails/SNL"
    )
    assert (
        first.headers["x-apikey"] == "KEY123"
        and "accessToken" not in str(first.url)
        and "KEY123" not in str(first.url)
    )
    assert first.url.params["numRows"] == "5" and first.url.params["filterCrs"] == "WAT"
    assert first.url.params["filterType"] == "to"
    assert "filterCrs" not in seen[1].url.params


def test_pascal_case_responses_parse_the_same() -> None:
    data = {"LocationName": "Stoneleigh", "TrainServices": [
        {"Std": "07:58", "Etd": "08:03", "Platform": "1", "Operator": "South Western Railway",
         "Destination": [{"LocationName": "London Waterloo"}], "DelayReason": "Signal failure"}]}  # fmt: skip
    (d,) = parse_services(data, NOW)
    assert d.destination == "London Waterloo" and d.status == "Delayed" and d.delay_minutes == 5
    assert d.cause == "Signal failure" and d.station == "Stoneleigh"


async def test_rows_are_capped_and_error_reason_is_reported() -> None:
    from app.integrations.rail.rdm import DEFAULT_BASE

    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(500, text="numRows must be between 1 and 10")

    async def nosleep(_: float) -> None: ...

    async with RdmClient(
        DEFAULT_BASE, "KEY", transport=httpx.MockTransport(handler), sleep=nosleep
    ) as c:
        with pytest.raises(RailError, match="HTTP 500: numRows must be between 1 and 10"):
            await c.departures("SNL", 20)
    assert seen[0].url.params["numRows"] == "10"

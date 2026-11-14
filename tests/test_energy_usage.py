"""The meter's half-hourly readings from Octopus: finding the meters, reading the answer, and each day priced."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.modules.octopus import usage as U

ACCOUNT = {"properties": [{"electricity_meter_points": [
    {"mpan": "1900000000001", "is_export": False, "meters": [{"serial_number": "21L0001"}]},
    {"mpan": "1900000000002", "is_export": True, "meters": [{"serial_number": "21L0001"}]}]}]}  # fmt: skip


def test_meters_and_readings() -> None:
    assert U.meters(ACCOUNT) == [{"mpan": "1900000000001", "serial": "21L0001", "direction": "import"},
                                 {"mpan": "1900000000002", "serial": "21L0001", "direction": "export"}]  # fmt: skip
    rows = U.readings({"results": [{"consumption": 0.5, "interval_start": "2026-11-13T16:00:00Z"},
                                   {"consumption": "x", "interval_start": "2026-11-13T16:30:00Z"}]})  # fmt: skip
    assert rows == [{"start": datetime(2026, 11, 13, 16, tzinfo=UTC), "kwh": 0.5}]


def test_each_day_priced_with_its_dear_hours() -> None:
    d0 = datetime(2026, 11, 13, tzinfo=UTC)
    cheap, std, peak = 13.4, 25.1, 37.6
    rates = [(d0, d0 + timedelta(hours=7), cheap), (d0 + timedelta(hours=7), d0 + timedelta(hours=16), std),
             (d0 + timedelta(hours=16), d0 + timedelta(hours=19), peak), (d0 + timedelta(hours=19), d0 + timedelta(days=1), std)]
    rows = [{"direction": "import", "start": d0 + timedelta(minutes=30 * i), "kwh": 1.0} for i in range(48)]
    rows += [{"direction": "export", "start": d0 + timedelta(hours=12), "kwh": 2.0}]
    day = U.days(rows, rates, ZoneInfo("Europe/London"))[0]
    assert (day["import_kwh"], day["export_kwh"], day["dear_kwh"]) == (48.0, 2.0, 6.0)
    assert day["cost_p"] == round(14 * cheap + 18 * std + 6 * peak + 10 * std, 2)
    assert day["by_rate"] == {"13.4": 14.0, "25.1": 28.0, "37.6": 6.0}


def test_readings_are_fetched_page_by_page_with_the_key() -> None:
    seen: list[str] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        assert req.headers["authorization"].startswith("Basic ")
        if "page=2" in str(req.url):
            return httpx.Response(200, json={"results": [{"consumption": 0.2, "interval_start": "2026-11-13T00:30:00Z"}], "next": None})
        return httpx.Response(200, json={"results": [{"consumption": 0.1, "interval_start": "2026-11-13T00:00:00Z"}],
                                         "next": "https://api.octopus.energy/v1/x/?page=2"})  # fmt: skip

    async def go() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            return await U.fetch(c, "sk_live", "1900000000001", "21L0001", datetime(2026, 11, 13, tzinfo=UTC), datetime(2026, 11, 14, tzinfo=UTC))

    rows = asyncio.run(go())
    assert [r["kwh"] for r in rows] == [0.1, 0.2] and "period_from=2026-11-13T00%3A00%3A00Z" in seen[0]

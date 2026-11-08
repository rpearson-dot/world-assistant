"""Crime and the police team for a point, from data.police.uk (faked here)."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx

from app import crime as C


def street(cat: str, name: str, outcome: str | None = None) -> dict[str, Any]:
    return {"category": cat, "month": "2026-09", "location": {"latitude": "51.4", "longitude": "-0.25", "street": {"id": 1, "name": name}},
            "outcome_status": {"category": outcome, "date": "2026-09"} if outcome else None}  # fmt: skip


class Fake:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path.removeprefix("/api")
        self.calls.append(p + ("?" + req.url.query.decode() if req.url.query else ""))
        if p == "/crime-last-updated":
            return httpx.Response(200, json={"date": "2026-09-01"})
        if p == "/crime-categories":
            return httpx.Response(200, json=[{"url": "anti-social-behaviour", "name": "Anti-social behaviour"},
                                             {"url": "burglary", "name": "Burglary"}, {"url": "vehicle-crime", "name": "Vehicle crime"}])  # fmt: skip
        if p == "/crimes-street/all-crime":
            m = req.url.params["date"]
            if m == "2026-09":
                return httpx.Response(200, json=[street("anti-social-behaviour", "On or near High Street"),
                                                 street("anti-social-behaviour", "On or near High Street"),
                                                 street("burglary", "On or near Park Road", "Investigation complete; no suspect identified"),
                                                 street("vehicle-crime", "On or near Park Road", "Awaiting court outcome")])  # fmt: skip
            return httpx.Response(200, json=[street("burglary", "x")] * 2)
        if p == "/locate-neighbourhood":
            return httpx.Response(200, json={"force": "metropolitan", "neighbourhood": "E05000405N"})
        if p == "/metropolitan/E05000405N":
            return httpx.Response(200, json={"name": "Old Malden", "url_force": "https://www.met.police.uk/a/your-area/x",
                                             "contact_details": {"telephone": "020 8721 2614", "email": "x@met.police.uk"},
                                             "locations": [{"name": "Kingston", "type": "station", "address": "5 High St", "postcode": "KT1"}]})  # fmt: skip
        if p.endswith("/priorities"):
            return httpx.Response(200, json=[{"issue": "<p>Old issue</p>", "issue-date": "2025-01-01T00:00:00"},
                                             {"issue": "<p>Vehicle crime &amp; theft</p>", "issue-date": "2026-08-01T00:00:00", "action": None}])  # fmt: skip
        if p.endswith("/events"):
            return httpx.Response(200, json=[{"title": "Past", "start_date": "2026-01-01T10:00:00"},
                                             {"title": "Cuppa with a copper", "start_date": "2026-11-20T10:00:00", "address": "Library"}])  # fmt: skip
        if p == "/forces/metropolitan":
            return httpx.Response(200, json={"name": "Metropolitan Police Service"})
        return httpx.Response(404)


def test_a_summary_with_a_trend_and_the_police_team() -> None:
    C._cache.clear()
    fake = Fake()

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
            team = await C.neighbourhood(client, 51.39, -0.26, today=date(2026, 11, 8))
            C._cache[("nbhd", 51.39, -0.26)] = (C.time.time(), team)
            return await C.look_up(51.39, -0.26, 3, client)

    d = asyncio.run(go())
    assert d["month"] == "2026-09" and d["from"] == "2026-07" and d["total"] == 8 and d["latest"] == 4
    assert d["by_category"][0] == {"name": "Burglary", "count": 5} and d["per_month"] == 2.7
    assert d["outcomes"] == {"not recorded": 6, "no one dealt with": 1, "still open": 1}
    assert [t["total"] for t in d["trend"]] == [2, 2, 4]
    t = d["neighbourhood"]
    assert t["name"] == "Old Malden" and t["force_name"] == "Metropolitan Police Service"
    assert t["priorities"][0]["issue"] == "Vehicle crime & theft"  # newest first, as text
    assert [e["title"] for e in t["events"]] == ["Cuppa with a copper"] and t["stations"][0]["name"] == "Kingston"
    s = C.words(d)
    assert s.startswith("In the 3 months to September 2026, 8 crimes were reported within a mile (about 2.7 a month; 4 in "
                        "September): most burglary (5), anti-social behaviour (2), vehicle crime (1).")
    assert "Old Malden" in s and "020 8721 2614" in s
    # asked again: from memory, not data.police.uk
    n = len(fake.calls)

    async def again() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
            await C.look_up(51.39, -0.26, 3, client)

    asyncio.run(again())
    assert len(fake.calls) == n


def test_months_back_crosses_the_year_and_outcomes_group() -> None:
    assert C.months_back("2026-02", 4) == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert C.outcome_group({"category": "Offender given a caution"}) == "someone dealt with"
    assert C.outcome_group(None) == "not recorded"


def test_the_api_route_and_errors(monkeypatch: Any) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    async def boom(*a: Any, **k: Any) -> Any:
        raise C.CrimeError("data.police.uk answered HTTP 500")

    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.setattr(C, "look_up", boom)
    c = TestClient(app)
    r = c.get("/v1/crime", params={"lat": 51.4, "lon": -0.25}, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 502 and "HTTP 500" in r.json()["detail"]
    assert c.get("/v1/crime", headers={"Authorization": "Bearer s3cret"}).status_code == 422


def test_too_many_requests_is_waited_out(monkeypatch: Any) -> None:
    C._cache.clear()
    monkeypatch.setattr(C, "GAP_S", 0)
    slept: list[float] = []

    async def nap(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(C.asyncio, "sleep", nap)
    n = {"calls": 0}

    def fake(req: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="Too Many Requests")
        return httpx.Response(200, json={"date": "2026-09-01"})

    async def go() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c:
            return await C.latest_month(c)

    assert asyncio.run(go()) == "2026-09" and slept == [2.0] and n["calls"] == 2

    def always(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    async def go2() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(always)) as c:
            await C._get(c, "/crime-categories")

    import pytest

    with pytest.raises(C.CrimeError, match="too many requests.*slow down"):
        asyncio.run(go2())

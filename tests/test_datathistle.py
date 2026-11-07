"""Data Thistle: its feed-format events read into the shape the other sources use, paged, and merged in."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from app.integrations.datathistle import client as DT

EVENT = {"event_id": 41, "name": "Comedy at the Rose", "category": "Comedy", "tags": ["stand-up"],
         "website": "https://www.rosetheatrekingston.org/",
         "descriptions": [{"type": "short", "description": "Three acts and a compère."}],
         "images": [{"url": "https://img.datathistle.com/41.jpg", "width": 600}],
         "schedules": [{"place_id": 7, "place": {"place_id": 7, "name": "Rose Theatre", "town": "Kingston upon Thames",
                                                  "postal_code": "KT1 1HL", "loc": {"latitude": "51.4087", "longitude": "-0.3066"}},
                        "performances": [
                            {"ts": "2026-11-12T20:00:00+00:00", "tickets": [{"type": "Standard", "currency": "GBP", "min_price": 15, "max_price": 22}],
                             "links": [{"type": "booking", "url": "https://www.rosetheatrekingston.org/book/41"}]},
                            {"ts": "2026-11-13T20:00:00+00:00", "tickets": [{"currency": "GBP", "min_price": 15}],
                             "properties": {"performance.sold-out": True}},
                            {"ts": "2026-12-30", "time_unknown": "Times tbc", "tickets": [{"currency": "GBP", "min_price": 0}]}]}]}  # fmt: skip
FAMILY = {"event_id": 42, "name": "Lantern Making", "tags": ["family", "workshop"],
          "schedules": [{"place_id": 8, "place": {"name": "Bourne Hall", "town": "Ewell", "loc": {"latitude": "51.35", "longitude": "-0.25"}},
                         "performances": [{"ts": "2026-11-14", "time_unknown": "Times vary", "tickets": [{"currency": "GBP", "min_price": 0, "max_price": 0}]}]}]}  # fmt: skip


def test_an_event_is_one_row_per_day_on() -> None:
    rows = DT.events_of(EVENT, date(2026, 11, 7), date(2026, 11, 14), (51.4027, -0.2560))
    assert [r["date"] for r in rows] == ["2026-11-12", "2026-11-13"]  # the 30 December one is outside the window
    a, b = rows
    assert (a["name"], a["venue"], a["town"], a["postcode"], a["type"]) == ("Comedy at the Rose", "Rose Theatre",
                                                                            "Kingston upon Thames", "KT1 1HL", "COMEDY")  # fmt: skip
    assert (a["doors_open"], a["price"], a["url"]) == ("20:00", "£15.00 to £22.00", "https://www.rosetheatrekingston.org/book/41")
    assert a["description"] == "Three acts and a compère." and a["image"] == "https://img.datathistle.com/41.jpg"
    assert 3 < a["distance_km"] < 4
    assert b["availability"] == "Sold out" and b["url"] == "https://www.rosetheatrekingston.org/"  # no booking link: its website
    free = DT.events_of(FAMILY, date(2026, 11, 7), date(2026, 11, 14))[0]
    assert (free["price"], free["doors_open"], free["availability"], free["type"]) == ("Free", None, "Times vary", "KIDS")


async def test_searched_paged_and_filtered() -> None:
    asked: list[str] = []

    def answer(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        assert req.headers["Authorization"] == "Bearer tok"
        if "page=2" in str(req.url):
            return httpx.Response(200, json=[FAMILY])
        return httpx.Response(200, json=[EVENT], headers={"X-Next": "https://api.datathistle.com/v1/events?page=2"})

    async with DT.DataThistleClient("tok", transport=httpx.MockTransport(answer)) as c:
        got = await c.search(51.4027, -0.2560, radius_km=8, first=date(2026, 11, 7), last=date(2026, 11, 14))
        assert "lat=51.4027" in asked[0] and "distance=8" in asked[0] and asked[1].endswith("page=2")
        assert [e["name"] for e in got["events"]] == ["Comedy at the Rose", "Comedy at the Rose", "Lantern Making"]
        kids = await c.search(51.4027, -0.2560, radius_km=8, first=date(2026, 11, 7), last=date(2026, 11, 14), event_type="KIDS")
        assert [e["name"] for e in kids["events"]] == ["Lantern Making"]
        word = await c.search(51.4027, -0.2560, radius_km=8, first=date(2026, 11, 7), last=date(2026, 11, 14), keyword="compère")
        assert {e["name"] for e in word["events"]} == {"Comedy at the Rose"}


async def test_refusals_in_words() -> None:
    for status, words in ((401, "refused the token"), (429, "usage limit"), (500, "HTTP 500")):
        t = httpx.MockTransport(lambda r, s=status: httpx.Response(s))
        async with DT.DataThistleClient("tok", transport=t) as c:
            with pytest.raises(DT.DataThistleError, match=words):
                await c.search(51.4, -0.25, radius_km=5, first=date(2026, 11, 7), last=date(2026, 11, 8))
    async with DT.DataThistleClient("") as c:
        with pytest.raises(DT.DataThistleError, match="datathistle_token"):
            await c.search(51.4, -0.25, radius_km=5, first=date(2026, 11, 7), last=date(2026, 11, 8))


async def test_merged_with_the_others_and_credited(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app import events as E
    from app.config import get_settings

    monkeypatch.setattr(type(get_settings()), "datathistle_token", property(lambda self: "tok"))
    monkeypatch.setattr(type(get_settings()), "skiddle_api_key", property(lambda self: ""))
    monkeypatch.setattr(type(get_settings()), "ticketmaster_api_key", property(lambda self: ""))

    class Search:
        requests = 0

        def __init__(self, token: str) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def search(self, *a: Any, **kw: Any) -> dict[str, Any]:
            calls.append(kw)
            Search.requests = 2  # two pages
            return {"total": 1, "events": DT.events_of(EVENT, kw["first"], kw["last"])}

    class Redis:
        def __init__(self) -> None:
            self.d: dict[str, Any] = {}

        async def get(self, k: str) -> Any:
            return self.d.get(k)

        async def set(self, k: str, v: Any, **kw: Any) -> None:
            self.d[k] = v

        async def incrby(self, k: str, n: int) -> None:
            self.d[k] = int(self.d.get(k) or 0) + n

        async def expire(self, k: str, s: int) -> None:
            return None

    calls: list[dict[str, Any]] = []
    redis = Redis()

    monkeypatch.setattr(E, "DataThistleClient", Search)
    req: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
    out = await E.search(req, "Home", 51.4027, -0.2560, radius_km=8, days=7, type=None, keyword=None, limit=10)
    assert out["sources"] == ["datathistle"] and out["events"][0]["source"] == "datathistle"
    assert out["attribution"][0]["name"] == "Data Thistle" and out["datathistle"]["used_this_month"] == 2
    # another ask there (other days, a word): from the shared 12-hour window, no request
    again = await E.search(req, "Home", 51.4027, -0.2560, radius_km=8, days=6, type="COMEDY", keyword="rose", limit=10)
    assert len(calls) == 1 and again["events"] and again["datathistle"]["used_this_month"] == 2
    # Home Assistant's repeating refreshes leave it out
    with pytest.raises(E.HTTPException, match="no events source"):
        await E.search(req, "Home", 51.4027, -0.2560, radius_km=8, days=7, type=None, keyword=None, limit=10, datathistle=False)
    # the allowance used: it says so, and asks nothing
    for k in list(redis.d):
        if not k.startswith("wa:datathistle:used"):
            del redis.d[k]
    monkeypatch.setattr(get_settings(), "datathistle_monthly_limit", 2)
    with pytest.raises(E.HTTPException, match="allowance is used"):  # the only source: it says why
        await E.search(req, "Home", 51.4027, -0.2560, radius_km=8, days=7, type=None, keyword=None, limit=10, cache_seconds=0)
    assert len(calls) == 1  # and nothing was asked


DB = __import__("os").environ.get("WA_TEST_DATABASE_URL")


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
async def test_the_token_is_renewed_and_both_new_ones_saved(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import json as _json
    from datetime import UTC, datetime, timedelta

    import sqlalchemy as sa
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app import secretstore

    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "datathistle_token.txt").write_text("old-access")
    (keys / "datathistle_refresh_token.txt").write_text("old-refresh")
    monkeypatch.setenv("WA_KEYS_DIR", str(keys))
    secretstore.clear_cache()
    sync = sa.create_engine(DB)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null, updated_at timestamptz)"))
    engine = create_async_engine(DB, poolclass=NullPool)  # type: ignore[arg-type]
    asked: list[tuple[str, str]] = []

    def auth(req: httpx.Request) -> httpx.Response:
        asked.append((str(req.url), req.headers["Authorization"]))
        return httpx.Response(200, json={"status": 200, "accessToken": f"access-{len(asked)}", "refreshToken": f"refresh-{len(asked)}"})

    t = httpx.MockTransport(auth)
    try:
        first = await DT.keep_alive(engine, transport=t)  # a new token: noted, renewed 25 days on
        assert first["renewed"] is False and first["why"].startswith("renews on") and asked == []
        st = await DT._state(engine)
        st["first_seen"] = (datetime.now(UTC) - timedelta(days=26)).isoformat()
        await DT._put_state(engine, st)
        done = await DT.keep_alive(engine, transport=t)
        assert done["renewed"] and asked == [("https://auth.datathistle.com/v1/refresh", "Bearer old-refresh")]
        assert (keys / "datathistle_token.txt").read_text() == "access-1"
        assert (keys / "datathistle_refresh_token.txt").read_text() == "refresh-1"
        assert (await DT.keep_alive(engine, transport=t))["renewed"] is False  # just renewed: not again
        # refused: renewed at once, with the new refresh token, but not more than hourly
        st = await DT._state(engine)
        st["tried_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        await DT._put_state(engine, st)
        assert (await DT.keep_alive(engine, refused=True, transport=t))["renewed"] and asked[-1][1] == "Bearer refresh-1"
        assert (await DT.keep_alive(engine, refused=True, transport=t))["why"] == "tried within the hour"
        # a refused refresh token: recorded, the saved tokens kept
        bad = httpx.MockTransport(lambda r: httpx.Response(401))
        with pytest.raises(DT.DataThistleError, match="refused the refresh token"):
            await DT.renew(engine, bad)
        assert "refused the refresh token" in (await DT._state(engine))["error"]
        assert (keys / "datathistle_token.txt").read_text() == "access-2"
        odd = httpx.MockTransport(lambda r: httpx.Response(200, json={"status": 200, "token": "x"}))
        with pytest.raises(DT.DataThistleError, match=r"did not include both tokens \(it sent \['status', 'token'\]\)"):
            await DT.renew(engine, odd)
        _json.dumps(await DT._state(engine))
    finally:
        await engine.dispose()
        with sync.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        sync.dispose()
        secretstore.clear_cache()


def test_the_live_apis_shape_is_read_too() -> None:
    """As the live API sent it on 7 November 2026: lat, lon and lng on the place, properties as lists, and a _distance
    in miles (not used: distances are worked out here)."""
    live = {"event_id": "3abff1a8", "name": "Harry Hill at Outside the Box Comedy Club", "tags": ["comedy"],
            "properties": [], "descriptions": [{"type": "third-party", "description": "Harry Hill, as seen on TV Burp."}],
            "schedules": [{"place_id": "55b6", "_distance": 0.48, "properties": [],
                           "place": {"name": "The Station", "town": "Epsom", "postal_code": "KT17 2JA",
                                     "lat": 51.36302, "lon": -0.24782, "lng": -0.24782, "_distance": 0.48},
                           "performances": [{"ts": "2026-11-10T18:45:00+00:00", "properties": ["performance.sold-out"],
                                             "tickets": [{"type": "Standard", "currency": "GBP", "min_price": 16}],
                                             "links": [{"url": "https://www.datathistle.com/details/x", "type": "booking"}]}]}]}  # fmt: skip
    (row,) = DT.events_of(live, date(2026, 11, 7), date(2026, 11, 14), (51.37, -0.247))
    assert (row["lat"], row["lon"], row["venue"], row["price"]) == (51.36302, -0.24782, "The Station", "£16.00")
    assert 0.7 < row["distance_km"] < 0.9 and row["availability"] == "Sold out"

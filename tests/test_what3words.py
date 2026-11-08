"""what3words: three words for a place (not counted), a place for three words (counted, stopped short of the free
plan's 100 a month), errors in words, and Here using both."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.integrations.what3words import client as W

ANSWER = {"country": "GB", "nearestPlace": "Epsom, Surrey", "words": "filled.count.soap", "language": "en",
          "coordinates": {"lat": 51.334312, "lng": -0.267801}, "map": "https://w3w.co/filled.count.soap"}  # fmt: skip


class Redis:
    def __init__(self) -> None:
        self.d: dict[str, Any] = {}

    async def get(self, k: str) -> Any:
        return self.d.get(k)

    async def incrby(self, k: str, n: int) -> None:
        self.d[k] = int(self.d.get(k) or 0) + n

    async def expire(self, k: str, s: int) -> None:
        return None


def test_three_words_are_recognised() -> None:
    for text in ("filled.count.soap", "///filled.count.soap", " ///Filled.Count.Soap ", "w3w: filled.count.soap"):
        assert W.words_in(text) == "filled.count.soap", text
    for text in ("KT17 1AA", "filled.count", "a.b.c.d", "filled count soap", "", "www.example.com/x"):
        assert W.words_in(text) is None, text


async def test_both_ways_with_the_key_in_a_header_and_only_one_way_counted() -> None:
    asked: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        asked.append(req)
        return httpx.Response(200, json=ANSWER)

    t = httpx.MockTransport(answer)
    got = await W.to_words(51.334312, -0.267801, key="k", transport=t)
    assert got == {"words": "filled.count.soap", "lat": 51.334312, "lon": -0.267801, "nearest": "Epsom, Surrey",
                   "country": "GB", "map": "https://w3w.co/filled.count.soap"}  # fmt: skip
    assert asked[0].url.path == "/v3/convert-to-3wa" and asked[0].url.params["coordinates"] == "51.334312,-0.267801"
    assert asked[0].headers["X-Api-Key"] == "k" and "key" not in asked[0].url.params  # never in the address
    redis = Redis()
    place = await W.to_place("///filled.count.soap", redis, key="k", limit=2, transport=t)
    assert (place["lat"], place["lon"]) == (51.334312, -0.267801) and asked[1].url.params["words"] == "filled.count.soap"
    assert await W.used_this_month(redis) == 1
    await W.to_place("filled.count.soap", redis, key="k", limit=2, transport=t)
    with pytest.raises(W.What3WordsError, match="this month's 2 three-word look-ups are used"):
        await W.to_place("filled.count.soap", redis, key="k", limit=2, transport=t)
    assert len(asked) == 3  # the refusal asked nothing


async def test_errors_in_words() -> None:
    cases = ((401, {"error": {"code": "InvalidKey", "message": "Authentication failed"}}, "refused the key"),
             (429, {"error": {"code": "QuotaExceeded", "message": "Quota exceeded"}}, "allowance is used \\(it said HTTP 429, QuotaExceeded"),
             (402, {"error": {"code": "QuotaExceeded", "message": "Quota exceeded or API plan does not have access"}},
              "its plan does not include this \\(it said HTTP 402"),
             (400, {"error": {"code": "BadWords", "message": "words must be three words"}}, "does not know that"),
             (500, {}, "HTTP 500"))  # fmt: skip
    for status, body, words in cases:
        t = httpx.MockTransport(lambda r, s=status, b=body: httpx.Response(s, json=b))
        with pytest.raises(W.What3WordsError, match=words):
            await W.to_place("filled.count.soap", key="k", transport=t)
    with pytest.raises(W.What3WordsError, match="not three words"):
        await W.to_place("KT17 1AA", key="k")
    with pytest.raises(W.What3WordsError, match="what3words_api_key"):
        await W.to_place("filled.count.soap", key="")


async def test_here_shows_the_words_and_takes_them(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from app import here as H
    from app.config import get_settings

    monkeypatch.setattr(type(get_settings()), "what3words_api_key", property(lambda self: "k"))

    async def to_words(lat: float, lon: float, **kw: Any) -> dict[str, Any]:
        return {"words": "filled.count.soap", "lat": lat, "lon": lon, "nearest": "Epsom, Surrey"}

    async def to_place(words: str, redis: Any = None, **kw: Any) -> dict[str, Any]:
        if words.strip("/") == "index.home.raft":
            return {"words": "index.home.raft", "lat": 51.52, "lon": -0.19, "nearest": "Bayswater, London"}
        if words.strip("/") == "table.lamp.post":
            return {"words": "table.lamp.post", "lat": 40.7, "lon": -74.0, "nearest": "New York"}
        raise W.What3WordsError("what3words does not know that (bad words)")

    monkeypatch.setattr(W, "to_words", to_words)
    monkeypatch.setattr(W, "to_place", to_place)
    seen: list[tuple[str, dict[str, str]]] = []
    from tests.test_here import fake_app

    req: Any = SimpleNamespace(app=fake_app(seen))
    d = await H.gather(req, 51.3343, -0.2678)
    assert d["what3words"]["words"] == "filled.count.soap"
    got: list[tuple[float, float]] = []

    async def gathered(request: Any, lat: float, lon: float) -> dict[str, Any]:
        got.append((lat, lon))
        return {"lat": lat, "lon": lon, "area": None, "parts": []}

    monkeypatch.setattr(H, "gather", gathered)
    req2: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=None, redis=None)))
    await H.here(req2, lat=None, lon=None, postcode=None, words="///index.home.raft", parts=None)
    await H.here(req2, lat=None, lon=None, postcode="index.home.raft", words=None, parts=None)  # in the postcode box
    assert got == [(51.52, -0.19), (51.52, -0.19)]
    with pytest.raises(HTTPException, match="outside Great Britain \\(near New York\\)"):
        await H.here(req2, lat=None, lon=None, postcode=None, words="table.lamp.post", parts=None)
    with pytest.raises(HTTPException) as e:
        await H.here(req2, lat=None, lon=None, postcode=None, words="no.such.words", parts=None)
    assert e.value.status_code == 404


async def test_without_a_key_here_has_no_words(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import here as H
    from app.config import get_settings
    from tests.test_here import fake_app

    monkeypatch.setattr(type(get_settings()), "what3words_api_key", property(lambda self: ""))
    req: Any = SimpleNamespace(app=fake_app([]))
    assert (await H.gather(req, 51.3343, -0.2678))["what3words"] is None


async def test_after_a_refusal_it_waits_before_asking_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """As what3words answered on 7 November 2026 (the free plan, the very first request)."""
    W._refused.clear()
    asked: list[str] = []

    def refuse(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        return httpx.Response(402, json={"error": {"code": "QuotaExceeded", "message": "Quota exceeded or API plan does not "
                                                   "have access to this feature."}})  # fmt: skip

    t = httpx.MockTransport(refuse)
    with pytest.raises(W.What3WordsError) as e:
        await W.to_words(51.37, -0.247, key="k", transport=t)
    assert e.value.brief == "not available on this key's plan" and "HTTP 402" in str(e.value)
    with pytest.raises(W.What3WordsError) as e2:
        await W.to_words(51.37, -0.247, key="k", transport=t)
    assert len(asked) == 1 and e2.value.brief.endswith("(checking again later)")  # not asked again
    W._refused["until"] = 0  # six hours on: asked again, and an answer clears it
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json=ANSWER))
    assert (await W.to_words(51.37, -0.247, key="k", transport=ok))["words"] == "filled.count.soap"
    assert W._refused == {}

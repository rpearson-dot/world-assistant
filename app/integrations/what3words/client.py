"""what3words: the three words for a 3 m square, and the square for three words (https://developer.what3words.com/).

    GET https://api.what3words.com/v3/convert-to-3wa?coordinates=LAT,LON&language=en     a place -> its words
    GET https://api.what3words.com/v3/convert-to-coordinates?words=a.b.c                  words -> the place

The key (secret `what3words_api_key`) goes in the X-Api-Key header, never the address. On the free plan the words for
a place are unlimited (fair use) but words to a place are 100 a month, so those are counted (`count`, in Redis, per
calendar month) and refused before `what3words_monthly_limit` (90) is reached.

    docker compose exec api python -m app.integrations.what3words.client 51.37 -0.247     what it sends
    docker compose exec api python -m app.integrations.what3words.client filled.count.soap  (one of the 100)
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings

API = "https://api.what3words.com/v3"
WORDS = re.compile(r"^\s*(?:///|w3w:\s*)?([a-z\u00c0-\u024f]+)[.。｡･・︒។։။۔።।]([a-z\u00c0-\u024f]+)[.。｡･・︒។։။۔።।]([a-z\u00c0-\u024f]+)\s*$",
                   re.IGNORECASE)  # fmt: skip


class What3WordsError(Exception):
    """Something to say in words: no key, the allowance used, words it does not know, or it could not be reached.
    `brief` is a short form for a page; `refused` is what3words declining the key or its plan."""

    def __init__(self, message: str, brief: str = "", refused: bool = False) -> None:
        super().__init__(message)
        self.brief = brief or message
        self.refused = refused


# After a refusal (the plan, the allowance, the key), the words for a place are not asked for again for a while: every
# Here look-up would otherwise be one more refused request. It is still tried again, in case the refusal was a mistake.
REFUSED_WAIT_S = 6 * 3600
_refused: dict[str, Any] = {}


def words_in(text: str) -> str | None:
    """'filled.count.soap' if the text is three words (with or without ///), else None."""
    m = WORDS.match(text or "")
    return ".".join(w.lower() for w in m.groups()) if m else None


def month_key() -> str:
    return f"wa:what3words:used:{datetime.now(ZoneInfo('Europe/London')).strftime('%Y-%m')}"


def _read(r: httpx.Response) -> dict[str, Any]:
    try:
        d = r.json()
    except ValueError:
        d = {}
    err = d.get("error") if isinstance(d, dict) else None
    if r.status_code >= 400 or err:
        code = str((err or {}).get("code") or "")
        why = str((err or {}).get("message") or f"HTTP {r.status_code}")
        said = f" (it said HTTP {r.status_code}" + (f", {code}" if code else "") + f": {why})"
        if r.status_code == 401 or code in ("InvalidKey", "MissingKey"):
            raise What3WordsError("what3words refused the key (check what3words_api_key on the Keys page)" + said,
                                  "the key was refused", refused=True)  # fmt: skip
        if r.status_code == 429:
            raise What3WordsError("what3words says this key's allowance is used" + said, "this key's allowance is used",
                                  refused=True)  # fmt: skip
        if code == "QuotaExceeded" or r.status_code in (402, 403) or code in ("PlanNotAllowed", "NotAllowed", "Forbidden"):
            # what3words says "Quota exceeded or API plan does not have access to this feature" for both
            raise What3WordsError("what3words declined: its allowance is used, or its plan does not include this" + said,
                                  "not available on this key's plan", refused=True)  # fmt: skip
        if code in ("BadWords", "BadCoordinates"):
            raise What3WordsError(f"what3words does not know that ({why})")
        raise What3WordsError(f"what3words answered: {why}")
    return d


def _place(d: dict[str, Any]) -> dict[str, Any]:
    c = d.get("coordinates") or {}
    return {"words": d.get("words"), "lat": c.get("lat"), "lon": c.get("lng"), "nearest": d.get("nearestPlace") or None,
            "country": d.get("country"), "map": d.get("map") or (f"https://w3w.co/{d['words']}" if d.get("words") else None)}  # fmt: skip


async def to_words(lat: float, lon: float, key: str = "", transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """The three words for a place. Not counted: unlimited on the free plan."""
    key = key or get_settings().what3words_api_key
    if not key:
        raise What3WordsError("no what3words_api_key saved")
    if _refused.get("until", 0) > time.monotonic():
        raise What3WordsError(_refused["why"], _refused["brief"] + " (checking again later)", refused=True)
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as c:
            r = await c.get(f"{API}/convert-to-3wa", params={"coordinates": f"{lat:.6f},{lon:.6f}", "language": "en"},
                            headers={"X-Api-Key": key})  # fmt: skip
    except httpx.HTTPError as exc:
        raise What3WordsError(f"what3words could not be reached ({type(exc).__name__})") from exc
    try:
        got = _place(_read(r))
    except What3WordsError as exc:
        if exc.refused:
            _refused.update(until=time.monotonic() + REFUSED_WAIT_S, why=str(exc), brief=exc.brief)
        raise
    _refused.clear()
    return got


async def to_place(words: str, redis: Any = None, key: str = "", limit: int | None = None,
                   transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:  # fmt: skip
    """The place three words name: one of the month's allowance, refused (without asking) once it is used."""
    w = words_in(words)
    if not w:
        raise What3WordsError(f"{words!r} is not three words like ///filled.count.soap")
    key = key or get_settings().what3words_api_key
    if not key:
        raise What3WordsError("no what3words_api_key saved: add it on the Keys page to look up three words")
    limit = get_settings().what3words_monthly_limit if limit is None else limit
    if redis is not None:
        used = int(await redis.get(month_key()) or 0)
        if used >= limit:
            raise What3WordsError(f"this month's {limit} three-word look-ups are used (what3words' free plan allows 100)")
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as c:
            r = await c.get(f"{API}/convert-to-coordinates", params={"words": w}, headers={"X-Api-Key": key})
    except httpx.HTTPError as exc:
        raise What3WordsError(f"what3words could not be reached ({type(exc).__name__})") from exc
    if redis is not None:
        await redis.incrby(month_key(), 1)  # an answer counts against the allowance, a refusal too
        await redis.expire(month_key(), 40 * 86400)
    return _place(_read(r))


async def used_this_month(redis: Any) -> int:
    return int(await redis.get(month_key()) or 0) if redis is not None else 0


async def _probe(arg: str) -> None:
    try:
        if words_in(arg):
            print(json.dumps(await to_place(arg), indent=2))
        else:
            lat, lon = (float(x) for x in arg.replace(",", " ").split())
            print(json.dumps(await to_words(lat, lon), indent=2))
    except What3WordsError as exc:
        print(f"FAILED: {exc}")


if __name__ == "__main__":
    asyncio.run(_probe(" ".join(sys.argv[1:]) or "51.5081 -0.0759"))

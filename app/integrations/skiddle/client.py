"""Skiddle events: what is on near a point.

Free API key from skiddle.com/api/join.php, kept in the skiddle_api_key secret. The key goes in the
`api_key` query parameter, so errors here never include the request URL.

Skiddle's published notes do not list the response fields, so parsing is defensive: every field is
optional. Run `python -m app.integrations.skiddle.probe` once to see the real shape.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any, Self

import httpx

from app.integrations.fuelfinder.queries import haversine_km

DEFAULT_BASE = "https://www.skiddle.com/api/v1"
KM_PER_MILE = 1.609344
MAX_LIMIT = 100
EVENT_CODES = (
    "FEST", "LIVE", "CLUB", "DATE", "THEATRE", "COMEDY", "EXHIB", "KIDS", "BARPUB", "LGB",
    "SPORT", "ARTS", "EXPER",
)  # fmt: skip


class SkiddleError(Exception):
    pass


def _text(value: Any, limit: int = 400) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] if s else None


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clock(value: Any) -> str | None:
    """HH:MM from '2026-10-22T19:30:00+00:00' or '19:30', ignoring the 00:00 placeholder."""
    m = re.search(r"(?:^|T)(\d{2}:\d{2})", str(value or ""))
    return m.group(1) if m and m.group(1) != "00:00" else None


def _price(raw: Any, currency: Any) -> str | None:
    p = _text(raw, 60)
    if p and p[0].isdigit() and str(currency or "").upper() == "GBP":
        p = "£" + p.replace(" - ", " - £")
    return p


def _availability(raw: dict[str, Any]) -> str | None:
    """Only what Skiddle says: cancelled, rescheduled, selling fast, or tickets listed."""
    if str(raw.get("cancelled")) == "1":
        return "Cancelled"
    if _text(raw.get("rescheduledDate")):
        return f"Rescheduled to {_text(raw.get('rescheduledDate'), 30)}"
    if raw.get("hotSeller") is True:
        return "Selling fast"
    if raw.get("tickets") is True:
        return "Tickets on sale"
    return None


def parse_event(raw: dict[str, Any], origin: tuple[float, float] | None = None) -> dict[str, Any]:
    """One Skiddle event as a flat, predictable dict. Missing fields become None."""
    venue = raw.get("venue") if isinstance(raw.get("venue"), dict) else {}
    opening = raw.get("openingtimes") if isinstance(raw.get("openingtimes"), dict) else {}
    lat = _num(venue.get("latitude")) if venue else None
    lon = _num(venue.get("longitude")) if venue else None
    dist = None
    if origin and lat is not None and lon is not None:
        dist = round(haversine_km(origin, (lat, lon)), 2)
    link = _text(raw.get("link"), 500)
    return {
        "id": _text(raw.get("id"), 40),
        "name": _text(raw.get("eventname") or raw.get("name"), 200),
        "date": _text(raw.get("date"), 40),
        "end_date": _text(raw.get("enddate"), 40),
        "doors_open": _clock(raw.get("startdate")) or _clock(opening.get("doorsopen")),
        "type": _text(raw.get("EventCode") or raw.get("eventcode"), 20),
        "venue": _text(venue.get("name"), 200),
        "town": _text(venue.get("town"), 100),
        "postcode": _text(venue.get("postcode"), 20),
        "lat": lat,
        "lon": lon,
        "distance_km": dist,
        "price": _price(raw.get("entryprice"), raw.get("currency")),
        "min_age": _text(raw.get("minage"), 10),
        "going": raw.get("goingtocount") if isinstance(raw.get("goingtocount"), int) else None,
        "description": _text(raw.get("description"), 300),
        "image": _text(raw.get("imageurl"), 500),
        "url": link if link and link.startswith("https://") else None,
        "availability": _availability(raw),
    }


class SkiddleClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE,
        *,
        limiter: Any | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 20.0,
    ) -> None:
        self._key = api_key
        self._limiter = limiter
        self._attempts = max_attempts
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
            follow_redirects=False,
        )  # fmt: skip

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        if not self._key:
            raise SkiddleError("the skiddle_api_key secret is not set")
        last = "no attempt"
        for attempt in range(1, self._attempts + 1):
            if self._limiter:
                await self._limiter.acquire(10.0)
            try:
                r = await self._http.get(path, params={**params, "api_key": self._key})
            except httpx.HTTPError as exc:
                last = type(exc).__name__  # never str(exc): it can carry the URL and so the key
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as exc:
                        raise SkiddleError("Skiddle returned a non-JSON response") from exc
                if r.status_code in (401, 403):
                    raise SkiddleError(f"Skiddle rejected the request (HTTP {r.status_code}); check the key")
                last = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < self._attempts:
                await self._sleep(min(2.0 * attempt, 6.0))
        raise SkiddleError(f"Skiddle request failed: {last}")

    async def search(
        self, lat: float, lon: float, *, radius_km: float = 8.0, min_date: date | None = None,
        max_date: date | None = None, event_code: str | None = None, keyword: str | None = None,
        limit: int = 20, order: str = "date",
    ) -> dict[str, Any]:  # fmt: skip
        """Events near a point. Latitude, longitude and radius (miles) must travel together."""
        params = {
            "latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}",
            "radius": str(max(1, math.ceil(radius_km / KM_PER_MILE))),
            "order": order, "limit": str(max(1, min(limit, MAX_LIMIT))),
        }  # fmt: skip
        if min_date:
            params["minDate"] = min_date.isoformat()
        if max_date:
            params["maxDate"] = max_date.isoformat()
        if event_code:
            code = event_code.strip().upper()
            if code not in EVENT_CODES:
                raise SkiddleError(f"unknown event type {event_code!r}; use one of {', '.join(EVENT_CODES)}")
            params["eventcode"] = code
        if keyword and keyword.strip():
            params["keyword"] = keyword.strip()[:80]
        data = await self._get("/events/search/", params)
        if not isinstance(data, dict):
            raise SkiddleError("unexpected response shape from Skiddle")
        if data.get("error") not in (None, 0, "0", False, ""):
            raise SkiddleError(f"Skiddle said: {_text(data.get('error'), 160)}")
        results = data.get("results")
        raw = results if isinstance(results, list) else []
        origin = (lat, lon)
        return {
            "total": _int(data.get("totalcount"), len(raw)),
            "events": [parse_event(r, origin) for r in raw if isinstance(r, dict)],
            "raw_keys": sorted(data.keys()),
        }

"""Ticketmaster Discovery API: concerts, sport, theatre and shows near a point.

Free consumer key from developer.ticketmaster.com, kept in the ticketmaster_api_key secret (the
consumer secret is not needed for searching). Default quota is 5,000 calls a day and 5 a second.
The key travels in the `apikey` query parameter, so errors here never include the request URL.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Self

import httpx

from app.integrations.fuelfinder.queries import haversine_km
from app.integrations.skiddle.client import _num, _text

DEFAULT_BASE = "https://app.ticketmaster.com/discovery/v2"
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
# Our event types (Skiddle's codes) to Ticketmaster classification names. Types with no equivalent
# (club nights, pubs and so on) are simply not searched there.
CLASSIFICATIONS = {
    "LIVE": "music", "FEST": "music", "COMEDY": "comedy", "THEATRE": "theatre", "SPORT": "sports",
    "KIDS": "family", "ARTS": "arts & theatre",
}  # fmt: skip


class TicketmasterError(Exception):
    pass


def geohash(lat: float, lon: float, precision: int = 9) -> str:
    """Standard geohash, which the Discovery API takes as `geoPoint`."""
    lat_r, lon_r = [-90.0, 90.0], [-180.0, 180.0]
    out, bits, bit, even = "", 0, 0, True
    while len(out) < precision:
        rng, val = (lon_r, lon) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        if val >= mid:
            bits = (bits << 1) | 1
            rng[0] = mid
        else:
            bits <<= 1
            rng[1] = mid
        even = not even
        bit += 1
        if bit == 5:
            out += _BASE32[bits]
            bits, bit = 0, 0
    return out


def _price(ranges: Any) -> str | None:
    if not isinstance(ranges, list) or not ranges or not isinstance(ranges[0], dict):
        return None
    lo, hi, cur = _num(ranges[0].get("min")), _num(ranges[0].get("max")), ranges[0].get("currency")
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(str(cur), f"{cur} " if cur else "")
    if lo is None:
        return None
    return f"{sym}{lo:.2f}" if hi is None or hi == lo else f"{sym}{lo:.2f} to {sym}{hi:.2f}"


_STATUS = {
    "onsale": "On sale", "offsale": "Off sale", "canceled": "Cancelled", "cancelled": "Cancelled",
    "postponed": "Postponed", "rescheduled": "Rescheduled",
}


def _availability(status: str | None, sales: Any, now: datetime) -> str | None:
    """What Discovery can honestly say: the sale status and window, never how many seats are left."""
    label = _STATUS.get((status or "").lower(), None)
    pub = sales.get("public") if isinstance(sales, dict) and isinstance(sales.get("public"), dict) else {}
    try:
        opens = datetime.fromisoformat(str(pub.get("startDateTime")))
        if opens.tzinfo is None:
            opens = opens.replace(tzinfo=UTC)
    except ValueError:
        opens = None
    if label in ("Cancelled", "Postponed", "Rescheduled"):
        return label
    if opens and opens > now:
        return f"On sale from {opens.date().isoformat()}"
    if label == "Off sale":
        return "Off sale: sold out, or sales have closed"
    return label


def parse_event(raw: dict[str, Any], origin: tuple[float, float] | None = None) -> dict[str, Any]:
    """One Ticketmaster event as the same flat dict the Skiddle events use."""
    embedded = raw.get("_embedded") if isinstance(raw.get("_embedded"), dict) else {}
    venues = embedded.get("venues") if isinstance(embedded.get("venues"), list) else []
    venue = venues[0] if venues and isinstance(venues[0], dict) else {}
    loc = venue.get("location") if isinstance(venue.get("location"), dict) else {}
    lat, lon = _num(loc.get("latitude")), _num(loc.get("longitude"))
    start = (raw.get("dates") or {}).get("start") if isinstance(raw.get("dates"), dict) else {}
    start = start if isinstance(start, dict) else {}
    cls = raw.get("classifications")
    seg = ""
    if isinstance(cls, list) and cls and isinstance(cls[0], dict):
        seg = ((cls[0].get("segment") or {}).get("name") or "") if isinstance(cls[0].get("segment"), dict) else ""
    link = _text(raw.get("url"), 500)
    city = venue.get("city") if isinstance(venue.get("city"), dict) else {}
    pc = venue.get("postalCode")
    status = ((raw.get("dates") or {}).get("status") or {}).get("code") if isinstance(raw.get("dates"), dict) else None
    return {
        "id": _text(raw.get("id"), 40),
        "name": _text(raw.get("name"), 200),
        "date": _text(start.get("localDate"), 40),
        "end_date": None,
        "doors_open": _text((start.get("localTime") or "")[:5], 10),
        "type": _text(seg, 30),
        "venue": _text(venue.get("name"), 200),
        "town": _text(city.get("name"), 100),
        "postcode": _text(pc, 20),
        "lat": lat,
        "lon": lon,
        "distance_km": round(haversine_km(origin, (lat, lon)), 2) if origin and lat is not None and lon is not None else None,
        "price": _price(raw.get("priceRanges")),
        "min_age": None,
        "going": None,
        "description": _text(raw.get("info") or raw.get("pleaseNote"), 300),
        "image": None,
        "url": link if link and link.startswith("https://") else None,
        "status": _text(status, 20),
        "availability": _availability(
            status if isinstance(status, str) else None, raw.get("sales"), datetime.now(UTC)
        ),
    }


class TicketmasterClient:
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
            raise TicketmasterError("the ticketmaster_api_key secret is not set")
        last = "no attempt"
        for attempt in range(1, self._attempts + 1):
            if self._limiter:
                await self._limiter.acquire(10.0)
            try:
                r = await self._http.get(path, params={**params, "apikey": self._key})
            except httpx.HTTPError as exc:
                last = type(exc).__name__  # never str(exc): it can carry the URL and so the key
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as exc:
                        raise TicketmasterError("Ticketmaster returned a non-JSON response") from exc
                if r.status_code in (401, 403):
                    raise TicketmasterError(
                        f"Ticketmaster rejected the request (HTTP {r.status_code}); check the key"
                    )
                last = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < self._attempts:
                await self._sleep(min(2.0 * attempt, 6.0))
        raise TicketmasterError(f"Ticketmaster request failed: {last}")

    async def search(
        self, lat: float, lon: float, *, radius_km: float = 8.0, start: datetime | None = None,
        end: datetime | None = None, event_type: str | None = None, keyword: str | None = None,
        limit: int = 20, page: int = 0,
    ) -> dict[str, Any]:  # fmt: skip
        params = {
            "geoPoint": geohash(lat, lon), "radius": f"{max(1, round(radius_km))}", "unit": "km",
            "size": str(max(1, min(limit, 100))), "sort": "date,asc", "countryCode": "GB",
        }  # fmt: skip
        if page > 0:
            params["page"] = str(page)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        if start:
            params["startDateTime"] = start.strftime(fmt)
        if end:
            params["endDateTime"] = end.strftime(fmt)
        if event_type:
            name = CLASSIFICATIONS.get(event_type.strip().upper())
            if name is None:
                return {"total": 0, "events": []}  # no Ticketmaster equivalent
            params["classificationName"] = name
        if keyword and keyword.strip():
            params["keyword"] = keyword.strip()[:80]
        data = await self._get("/events.json", params)
        if not isinstance(data, dict):
            raise TicketmasterError("unexpected response shape from Ticketmaster")
        emb = data.get("_embedded") if isinstance(data.get("_embedded"), dict) else {}
        raw = emb.get("events") if isinstance(emb.get("events"), list) else []
        page = data.get("page") if isinstance(data.get("page"), dict) else {}
        total = page.get("totalElements")
        return {
            "total": total if isinstance(total, int) else len(raw),
            "events": [parse_event(r, (lat, lon)) for r in raw if isinstance(r, dict)],
        }

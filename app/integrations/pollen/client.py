"""Pollen forecast from the Austrian Pollen Information Service (polleninformation.at).

Personal API key (request it through the contact form on polleninformation.at/en/data-interface),
kept in the pollen_api_key secret. Non-commercial use only, with the credit "Austrian Pollen
Information Service, www.polleninformation.at". The key allows 40 requests a day (see app/pollen_budget.py).

The key travels in the `apikey` query parameter, so errors here never include the request URL.
Response (documented): `contamination` = allergens with `poll_id`, `poll_title`, `contamination_1..4`
(0 none to 4 very high; 1 is today); `allergyrisk` = `allergyrisk_1..4` (0 to 10);
`allergyrisk_hourly` = `allergyrisk_hourly_1..4`, each 24 hourly values. Run
`python -m app.integrations.pollen.probe` once to see the real shape.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any, Self

import httpx

DEFAULT_BASE = "https://www.polleninformation.at/api/forecast/public"
LEVELS = ("none", "low", "moderate", "high", "very high")
DAYS = 4
ATTRIBUTION = "Austrian Pollen Information Service, www.polleninformation.at"


class PollenError(Exception):
    pass


def _text(value: Any, limit: int = 100) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] if s else None


def _number(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _level(value: Any) -> int | None:
    f = _number(value)
    return None if f is None else max(0, min(4, round(f)))


def risk_level(risk: float | None) -> int | None:
    """Allergy risk 0-10 on the 0-4 scale the allergens use (the same round(value / 2.5))."""
    return None if risk is None else max(0, min(4, round(risk / 2.5)))


def label(level: int | None) -> str | None:
    return None if level is None else LEVELS[level]


def parse_forecast(raw: Any, today: date) -> dict[str, Any]:
    """Flat, predictable forecast. Missing parts become None or are left out."""
    if not isinstance(raw, dict):
        raise PollenError("unexpected response shape from Polleninformation")
    if raw.get("error"):
        msg = _text(raw.get("error"), 80) or "error"
        raise PollenError(f"Polleninformation said: {msg}")
    dates = [(today + timedelta(days=i)).isoformat() for i in range(DAYS)]
    allergens = []
    for a in raw.get("contamination") or []:
        if not isinstance(a, dict):
            continue
        levels = [_level(a.get(f"contamination_{i}")) for i in range(1, DAYS + 1)]
        allergens.append(
            {
                "id": _text(a.get("poll_id"), 40),
                "name": _text(a.get("poll_title")),
                "levels": levels,
                "labels": [label(v) for v in levels],
            }
        )
    risk_raw = raw.get("allergyrisk") if isinstance(raw.get("allergyrisk"), dict) else {}
    risk = [_number(risk_raw.get(f"allergyrisk_{i}")) for i in range(1, DAYS + 1)]
    hourly_raw = raw.get("allergyrisk_hourly") if isinstance(raw.get("allergyrisk_hourly"), dict) else {}
    hourly: list[list[float | None] | None] = []
    for i in range(1, DAYS + 1):
        vals = hourly_raw.get(f"allergyrisk_hourly_{i}")
        hourly.append([_number(v) for v in vals[:24]] if isinstance(vals, list) else None)
    scaled = [risk_level(r) for r in risk]
    return {
        "dates": dates,
        "allergens": allergens,
        "allergy_risk": risk,
        "allergy_risk_level": scaled,
        "allergy_risk_label": [label(v) for v in scaled],
        "allergy_risk_hourly": hourly,
    }


class PollenClient:
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
        self._base = base_url
        self._limiter = limiter
        self._attempts = max_attempts
        self._sleep = sleep
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def raw(self, lat: float, lon: float, *, country: str = "GB", lang: str = "en") -> Any:
        if not self._key:
            raise PollenError("the pollen_api_key secret is not set")
        params = {
            "country": country.upper(), "lang": lang, "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}", "apikey": self._key,
        }  # fmt: skip
        last = "no attempt"
        for attempt in range(1, self._attempts + 1):
            if self._limiter:
                await self._limiter.acquire(10.0)
            try:
                r = await self._http.get(self._base, params=params)
            except httpx.HTTPError as exc:
                last = type(exc).__name__  # never str(exc): it can carry the URL and so the key
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as exc:
                        raise PollenError("Polleninformation returned a non-JSON response") from exc
                if r.status_code in (401, 403):
                    raise PollenError(f"Polleninformation rejected the request (HTTP {r.status_code}); check the key")
                last = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < self._attempts:
                await self._sleep(min(2.0 * attempt, 6.0))
        raise PollenError(f"Polleninformation request failed: {last}")

    async def forecast(
        self, lat: float, lon: float, today: date, *, country: str = "GB", lang: str = "en"
    ) -> dict[str, Any]:
        return parse_forecast(await self.raw(lat, lon, country=country, lang=lang), today)

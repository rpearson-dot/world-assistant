"""National Rail live departures from the Rail Data Marketplace (Darwin Live Departure Board).

Register for a consumer account, subscribe to the "Live Departure Board" product and put its
consumer key in the nr_access_token secret. The key is sent in the x-apikey header.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Self
from zoneinfo import ZoneInfo

import httpx

from app.integrations.rail.model import Departure

DEFAULT_BASE = "https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120"
_CRS = re.compile(r"^[A-Za-z]{3}$")
MAX_ROWS = 10  # the public Live Departure Board refuses more than this per request


class RailError(Exception):
    pass


def _reason(r: httpx.Response) -> str:
    """A short plain-text reason from an error response (the key is never echoed back)."""
    try:
        text = r.text.strip().replace("\n", " ")
    except Exception:  # noqa: BLE001
        return ""
    return f": {text[:140]}" if text else ""


class RdmClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        limiter: Any | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 20.0,
    ) -> None:
        self._token = token
        self._limiter = limiter
        self._attempts = max_attempts
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        headers = {"x-apikey": self._token} if self._token else {}
        last = "no attempt"
        for attempt in range(1, self._attempts + 1):
            if self._limiter:
                await self._limiter.acquire(10.0)
            try:
                r = await self._http.get(path, params=params or {}, headers=headers)
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as exc:
                        raise RailError("rail server returned a non-JSON response") from exc
                if r.status_code in (400, 404):
                    raise RailError("not found (check the station code, e.g. SNL)")
                if r.status_code in (401, 403):
                    raise RailError(
                        f"rail server rejected the request (HTTP {r.status_code}); check the access key"
                    )
                last = f"HTTP {r.status_code}{_reason(r)}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < self._attempts:
                await self._sleep(min(2.0 * attempt, 6.0))
        raise RailError(f"rail request failed: {last}")

    async def departures(
        self, crs: str, rows: int = 15, to_crs: str | None = None
    ) -> dict[str, Any]:
        if not _CRS.match(crs) or (to_crs and not _CRS.match(to_crs)):
            raise RailError("station codes are three letters, e.g. SNL")
        params = {"numRows": str(max(1, min(rows, MAX_ROWS)))}
        if to_crs:
            params.update(filterCrs=to_crs.upper(), filterType="to")
        data = await self.get(f"/GetDepBoardWithDetails/{crs.upper()}", params)
        if not isinstance(data, dict):
            raise RailError("unexpected response shape for departures")
        return data

    async def search_stations(self, query: str) -> list[dict[str, Any]]:
        # The marketplace service has no station search; a three-letter code is typed in directly.
        if _CRS.match(query.strip()):
            return [{"stationName": query.strip().upper(), "crsCode": query.strip().upper()}]
        raise RailError("station search is not available; enter the three-letter code, e.g. SNL")


def _hhmm(value: Any, now: datetime, tz: ZoneInfo) -> datetime | None:
    """'07:42' to an aware datetime on the nearest sensible day (handles the midnight wrap)."""
    if not isinstance(value, str) or not re.fullmatch(r"\d\d:\d\d", value):
        return None
    local_now = now.astimezone(tz)
    h, m = int(value[:2]), int(value[3:])
    dt = local_now.replace(hour=h, minute=m, second=0, microsecond=0)
    if dt < local_now - timedelta(hours=6):
        dt += timedelta(days=1)
    elif dt > local_now + timedelta(hours=18):
        dt -= timedelta(days=1)
    return dt.astimezone(UTC)


def _lower(value: Any) -> Any:
    """Lower-case every key, so camelCase and PascalCase responses parse the same way."""
    if isinstance(value, dict):
        return {str(k).lower(): _lower(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_lower(v) for v in value]
    return value


def parse_services(
    data: dict[str, Any], now: datetime, tz_name: str = "Europe/London"
) -> list[Departure]:
    """Rail Data Marketplace departure-board JSON to Departure records.
    Tolerant: key case does not matter and missing fields become None."""
    data = _lower(data)
    tz = ZoneInfo(tz_name)
    station = data.get("locationname") if isinstance(data.get("locationname"), str) else None
    out = []
    for svc in data.get("trainservices") or []:
        if not isinstance(svc, dict):
            continue
        dests = [d.get("locationname") for d in svc.get("destination") or [] if isinstance(d, dict)]
        scheduled = _hhmm(svc.get("std"), now, tz)
        etd = svc.get("etd")
        expected, status, cause = scheduled, None, None
        if svc.get("iscancelled") or etd == "Cancelled":
            status, cause = "Cancelled", svc.get("cancelreason")
        elif isinstance(etd, str) and etd.lower() == "on time":
            status = "OnTime"
        elif isinstance(etd, str) and etd.lower() == "delayed":
            status, cause = "Delayed", svc.get("delayreason")
        elif (t := _hhmm(etd, now, tz)) is not None:
            expected = t
            status = "OnTime" if scheduled and t <= scheduled else "Delayed"
            cause = svc.get("delayreason") if status == "Delayed" else None
        plat = svc.get("platform")
        out.append(Departure(
            station=station, line=svc.get("operator") if isinstance(svc.get("operator"), str) else None,
            destination=" and ".join(d for d in dests if d) or None,
            platform=str(plat) if plat not in (None, "") else None,
            scheduled=scheduled, expected=expected, status=status,
            cause=cause if isinstance(cause, str) and cause else None,
        ))  # fmt: skip
    return out

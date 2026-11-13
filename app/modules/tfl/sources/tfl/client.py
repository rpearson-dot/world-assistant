"""Transport for London Unified API client (api.tfl.gov.uk)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, Self

import httpx

BASE_URL = "https://api.tfl.gov.uk"


class Limiter(Protocol):
    async def acquire(self, max_wait: float = ...) -> None: ...


class TflError(Exception):
    pass


class TflClient:
    """GET helper. The app key is optional (TfL allows a low unauthenticated rate)."""

    def __init__(
        self,
        app_key: str = "",
        *,
        base_url: str = BASE_URL,
        limiter: Limiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 20.0,
    ) -> None:
        self._key = app_key
        self._limiter = limiter
        self._attempts = max_attempts
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=transport, follow_redirects=False
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = dict(params or {})
        if self._key:
            query["app_key"] = self._key
        last = "no attempt"
        for attempt in range(1, self._attempts + 1):
            if self._limiter:
                await self._limiter.acquire(10.0)
            try:
                r = await self._http.get(path, params=query)
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as exc:
                        raise TflError("TfL returned a non-JSON response") from exc
                if r.status_code == 404:
                    raise TflError("not found (check the station and line ids)")
                if r.status_code in (401, 403):
                    raise TflError(
                        f"TfL rejected the request (HTTP {r.status_code}); check the app key"
                    )
                last = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < self._attempts:
                await self._sleep(min(2.0 * attempt, 6.0))
        raise TflError(f"TfL request failed: {last}")

    async def arrival_departures(self, station: str, lines: list[str]) -> list[dict[str, Any]]:
        data = await self.get(
            f"/StopPoint/{station}/ArrivalDepartures", {"lineIds": ",".join(lines)}
        )
        if not isinstance(data, list):
            raise TflError("unexpected response shape for ArrivalDepartures")
        return [d for d in data if isinstance(d, dict)]

    async def line_status(self, lines: list[str]) -> list[dict[str, Any]]:
        data = await self.get(f"/Line/{','.join(lines)}/Status")
        if not isinstance(data, list):
            raise TflError("unexpected response shape for Line status")
        return [d for d in data if isinstance(d, dict)]

    async def search_stops(
        self, query: str, modes: str = "", limit: int = 10
    ) -> list[dict[str, Any]]:
        params = {"maxResults": str(limit)}
        if modes:
            params["modes"] = modes
        data = await self.get(f"/StopPoint/Search/{query}", params)
        matches = data.get("matches") if isinstance(data, dict) else None
        return [m for m in matches or [] if isinstance(m, dict)]

    async def line_arrivals(self, line: str) -> list[dict[str, Any]]:
        data = await self.get(f"/Line/{line}/Arrivals")
        if not isinstance(data, list):
            raise TflError("unexpected response shape for Line arrivals")
        return [d for d in data if isinstance(d, dict)]

    async def route_sequence(self, line: str, direction: str) -> list[dict[str, str]]:
        """Stops of the longest branch of a route, in order: [{id, name}]."""
        data = await self.get(f"/Line/{line}/Route/Sequence/{direction}")
        seqs = data.get("stopPointSequences") if isinstance(data, dict) else None
        best: list[dict[str, str]] = []
        for seq in seqs or []:
            stops = [
                {"id": s["id"], "name": str(s.get("name") or "")}
                for s in (seq.get("stopPoint") or [])
                if isinstance(s, dict) and isinstance(s.get("id"), str)
            ]
            if len(stops) > len(best):
                best = stops
        return best

    async def timetable_times(self, line: str, stop: str, direction: str) -> list[str]:
        """Scheduled Monday-to-Friday departure times ('HH:MM') from a stop, sorted."""
        data = await self.get(f"/Line/{line}/Timetable/{stop}", {"direction": direction})
        routes = (data.get("timetable") or {}).get("routes") if isinstance(data, dict) else None
        times: set[str] = set()
        for route in routes or []:
            for sched in route.get("schedules") or []:
                name = str(sched.get("name") or "")
                if not name.startswith("Monday"):
                    continue
                for j in sched.get("knownJourneys") or []:
                    try:
                        times.add(f"{int(j['hour']) % 24:02d}:{int(j['minute']):02d}")
                    except (KeyError, TypeError, ValueError):
                        continue
        return sorted(times)

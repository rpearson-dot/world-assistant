"""GRIDSERVE's open data API (published under the Public Charge Point Regulations 2023).

    GET https://api.gridserve.com/ocpi/v1/locations          every site, with its chargers (EVSEs) and their status
    GET https://api.gridserve.com/ocpi/v1/tariffs            the prices the connectors refer to

The key goes in an `ec-subscription-key` header, never the URL. The objects are OCPI's (Location, EVSE, Connector,
Tariff), but the answer is a bare list rather than OCPI's {"data": ..., "status_code": ...} envelope; both are read,
and so is the {"locations": [...]} shape the developer document shows.
"""

from __future__ import annotations

from typing import Any, Self

import httpx

BASE = "https://api.gridserve.com/ocpi/v1"


class GridserveError(Exception):
    """The API could not be read or refused the key."""


def unwrap(body: Any, name: str) -> list[dict[str, Any]]:
    """The list of objects, whichever shape it came in."""
    if isinstance(body, dict):
        for key in (name, "data"):
            if isinstance(body.get(key), list):
                body = body[key]
                break
    if not isinstance(body, list):
        raise GridserveError(f"GRIDSERVE's {name} answer was not a list")
    return [x for x in body if isinstance(x, dict)]


class GridserveClient:
    def __init__(self, api_key: str, base: str = BASE, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 120.0) -> None:  # fmt: skip
        if not api_key:
            raise GridserveError("no GRIDSERVE key: save it as gridserve_api_key (Settings > Keys)")
        self._http = httpx.AsyncClient(base_url=base, headers={"ec-subscription-key": api_key, "Accept": "application/json"},
                                       timeout=httpx.Timeout(30.0, read=timeout), transport=transport,
                                       follow_redirects=True)  # fmt: skip

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _get(self, path: str, name: str) -> list[dict[str, Any]]:
        try:
            r = await self._http.get(path)
        except httpx.HTTPError as exc:
            raise GridserveError(f"GRIDSERVE's API could not be reached: {type(exc).__name__}") from exc
        if r.status_code in (401, 403):
            raise GridserveError("GRIDSERVE's API refused the key: check gridserve_api_key (Settings > Keys)")
        if r.status_code == 429:
            raise GridserveError("GRIDSERVE's API asked us to slow down (HTTP 429)")
        if r.status_code != 200:
            raise GridserveError(f"GRIDSERVE's API answered HTTP {r.status_code}")
        try:
            return unwrap(r.json(), name)
        except ValueError as exc:
            raise GridserveError("GRIDSERVE's API did not answer with JSON") from exc

    async def locations(self) -> list[dict[str, Any]]:
        return await self._get("/locations", "locations")

    async def tariffs(self) -> list[dict[str, Any]]:
        return await self._get("/tariffs", "tariffs")

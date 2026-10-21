"""Client for the National Highways data services (api.data.nationalhighways.co.uk).

All four services share one host, one key header and one rate limit (10 calls
per key per minute), so every request draws a slot from a shared limiter first.
Responses are requested as JSON (the default is XML).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, Self
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger("wa.nh")

DEFAULT_BASE_URL = "https://api.data.nationalhighways.co.uk"

CLOSURES = "/roads/v2.0/closures"
SPEED_MANAGED_AREAS = "/sma/v1.0/speedManagedAreas"
VMS = "/dvms/v1.0/vms"
ROAD_LIMITS_AND_FEATURES = "/rlf/v1.0/roadLimitsAndFeatures"

_RETRYABLE_STATUS = {500, 502, 503, 504}
_MAX_BODY_TEXT = 1_000_000  # characters kept when a body is not valid JSON


class Limiter(Protocol):
    async def acquire(self, max_wait: float = ...) -> None: ...


def iso(dt: datetime) -> str:
    """Format as the API expects: YYYY-MM-DDThh:mm:ss (UTC; unverified for non-VMS)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def cursor_from_x_next(value: str | None) -> str | None:
    """Extract the next-page cursor from the `x-next` response header.

    The header is documented as "a link to the next page". It may be an absolute
    URL carrying a pageCursor parameter, or the bare cursor. We only ever take
    the cursor value and call our own base URL with it, so a surprising host in
    that header can never receive the subscription key.
    """
    if not value:
        return None
    value = value.strip()
    if "://" in value or value.startswith("/") or "?" in value:
        query = parse_qs(urlparse(value).query)
        lowered = {k.lower(): v for k, v in query.items()}
        found = lowered.get("pagecursor")
        return found[0] if found else None
    return value or None


@dataclass(frozen=True)
class Page:
    index: int
    path: str
    params: dict[str, str]
    status_code: int | None
    x_next: str | None
    elapsed_ms: int
    body_bytes: int
    json: Any | None
    text: str | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.error is None

    @property
    def rate_limited(self) -> bool:
        return self.status_code == 429


class NationalHighwaysClient:
    def __init__(
        self,
        api_key: str,
        limiter: Limiter,
        base_url: str = DEFAULT_BASE_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
        limiter_max_wait: float = 30.0,
        max_attempts: int = 2,
        retry_delay: float = 2.0,
    ) -> None:
        self._limiter = limiter
        self._limiter_max_wait = limiter_max_wait
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Ocp-Apim-Subscription-Key": api_key,
                "X-Response-MediaType": "application/json",
                "X-Data-Format": "DATEXII",
                "Accept": "application/json",
            },
            timeout=timeout,
            transport=transport,
            follow_redirects=False,  # never forward the key to another host
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def pages(
        self, path: str, params: dict[str, str] | None = None, *, max_pages: int = 1
    ) -> AsyncIterator[Page]:
        """Yield up to max_pages pages, following the x-next cursor.

        The first request carries no pageCursor, as the API requires. Iteration
        stops on an error page, when no x-next is returned, or at max_pages.
        """
        base = dict(params or {})
        cursor: str | None = None
        for index in range(max_pages):
            query = dict(base)
            if cursor is not None:
                query["pageCursor"] = cursor
            page = await self._get(path, query, index)
            yield page
            if not page.ok:
                return
            cursor = cursor_from_x_next(page.x_next)
            if cursor is None:
                return

    async def _get(self, path: str, params: dict[str, str], index: int) -> Page:
        attempt = 0
        while True:
            attempt += 1
            await self._limiter.acquire(self._limiter_max_wait)
            started = time.monotonic()
            try:
                resp = await self._client.get(path, params=params)
            except httpx.TransportError as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                if attempt < self._max_attempts:
                    log.warning("nh transport error on %s (attempt %d): %s", path, attempt, exc)
                    await asyncio.sleep(self._retry_delay)
                    continue
                return Page(
                    index, path, params, None, None, elapsed, 0, None, None,
                    f"{type(exc).__name__}: {exc}",
                )  # fmt: skip
            elapsed = int((time.monotonic() - started) * 1000)
            if resp.status_code in _RETRYABLE_STATUS and attempt < self._max_attempts:
                log.warning("nh HTTP %d on %s (attempt %d)", resp.status_code, path, attempt)
                await asyncio.sleep(self._retry_delay)
                continue
            return self._to_page(resp, path, params, index, elapsed)

    @staticmethod
    def _to_page(
        resp: httpx.Response, path: str, params: dict[str, str], index: int, elapsed_ms: int
    ) -> Page:
        body_json: Any | None = None
        body_text: str | None = None
        try:
            body_json = resp.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body_text = resp.text[:_MAX_BODY_TEXT]
        error = None if resp.status_code == 200 else f"HTTP {resp.status_code}"
        return Page(
            index=index,
            path=path,
            params=params,
            status_code=resp.status_code,
            x_next=resp.headers.get("x-next"),
            elapsed_ms=elapsed_ms,
            body_bytes=len(resp.content),
            json=body_json,
            text=body_text,
            error=error,
        )

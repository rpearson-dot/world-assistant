"""Client for the UK government Fuel Finder API.

Facts this relies on (from public notes by other users; verify against your portal):

* Base URL https://www.fuel-finder.service.gov.uk/api/v1, UK IP addresses only.
* Token: POST form (grant_type, client_id, client_secret, scope) to
  /oauth/generate_access_token; the token is at data.access_token and lasts ~1 hour.
* Data: GET /pfs and GET /pfs/fuel-prices with batch-number=1,2,...; 500 records per
  batch. /pfs returns a bare JSON list (confirmed live); a {"data": [...]} wrapper is
  also accepted. A 404 means there are no more batches. An
  empty 200 is NOT the end: it is a transient fault and must be retried.
* One request at a time, at most 100 a minute.
* Bad credentials lock the client out for about 5 minutes, so the token request is
  never retried automatically.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, Self

import httpx

log = logging.getLogger("wa.fuel.client")

BASE_URL = "https://www.fuel-finder.service.gov.uk/api/v1"
TOKEN_PATH = "/oauth/generate_access_token"
STATIONS = "/pfs"
PRICES = "/pfs/fuel-prices"
SCOPE = "fuelfinder.read"
DEFAULT_TOKEN_SECONDS = 3600
TOKEN_MARGIN_SECONDS = 120


class Limiter(Protocol):
    async def acquire(self, max_wait: float = ...) -> None: ...


class FuelFinderError(Exception):
    """Base class for Fuel Finder failures."""


class AuthError(FuelFinderError):
    """The token request was refused. Not retried: repeated failures cause a lockout."""


class IncompleteError(FuelFinderError):
    """A batch kept failing, so the data would be a truncated pull."""


@dataclass(frozen=True)
class Batch:
    index: int
    records: list[dict[str, Any]]
    raw: Any  # the whole response body (a list or an object), for capture


def describe_body(body: Any) -> str:
    """A short, secret-free description of a response body for logs."""
    if isinstance(body, dict):
        return "keys " + ", ".join(f"{k}:{type(v).__name__}" for k, v in list(body.items())[:12])
    if isinstance(body, list):
        return f"list of {len(body)}"
    return repr(body)[:80]


class FuelFinderClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        base_url: str = BASE_URL,
        limiter: Limiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        pause_seconds: float = 0.5,
        max_attempts: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 60.0,
        limiter_max_wait: float = 30.0,
    ) -> None:
        self._limiter_max_wait = limiter_max_wait
        self._id = client_id
        self._secret = client_secret
        self._limiter = limiter
        self._pause = pause_seconds
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=transport, follow_redirects=False
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _token_value(self, *, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token and time.monotonic() < self._token_expires:
                return self._token
            if self._limiter:
                await self._limiter.acquire(self._limiter_max_wait)
            try:
                resp = await self._http.post(
                    TOKEN_PATH,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._id,
                        "client_secret": self._secret,
                        "scope": SCOPE,
                    },
                )
            except httpx.TransportError as exc:
                raise AuthError(f"token request failed: {type(exc).__name__}") from exc
            if resp.status_code != 200:
                raise AuthError(f"token request returned HTTP {resp.status_code}")
            try:
                data = resp.json()["data"]
                token = data["access_token"]
            except (ValueError, KeyError, TypeError) as exc:
                raise AuthError("token response had no data.access_token") from exc
            lifetime = data.get("expires_in") if isinstance(data, dict) else None
            seconds = float(lifetime) if isinstance(lifetime, int | float) else DEFAULT_TOKEN_SECONDS
            self._token = str(token)
            self._token_expires = time.monotonic() + max(seconds - TOKEN_MARGIN_SECONDS, 60.0)
            return self._token

    async def _get_batch(self, path: str, index: int) -> Batch | None:
        """One batch, with retries. Returns None when the API says there are no more."""
        refreshed = False
        for attempt in range(1, self._max_attempts + 1):
            if self._limiter:
                await self._limiter.acquire(self._limiter_max_wait)
            token = await self._token_value()
            try:
                resp = await self._http.get(
                    path, params={"batch-number": index}, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.TransportError as exc:
                log.warning("%s batch %d: %s (attempt %d)", path, index, type(exc).__name__, attempt)
                await self._sleep(2**attempt)
                continue
            status = resp.status_code
            if status == 404:
                return None
            if status == 401 and not refreshed:
                refreshed = True
                await self._token_value(force=True)
                continue
            if status == 429:
                await self._sleep(10 * attempt)
                continue
            if status >= 500:
                await self._sleep(2**attempt)
                continue
            if status != 200:
                raise FuelFinderError(f"{path} batch {index} returned HTTP {status}")
            try:
                body = resp.json()
            except ValueError:
                body = None
            # Live responses are a bare JSON list; some notes show {"data": [...]}. Accept both.
            records = body if isinstance(body, list) else body.get("data") if isinstance(body, dict) else None
            if records:
                return Batch(index, list(records), body)
            log.warning(
                "%s batch %d: empty 200 (attempt %d); body is %s, %s",
                path, index, attempt, type(body).__name__, describe_body(body),
            )  # fmt: skip
            await self._sleep(2**attempt)
        raise IncompleteError(f"{path} batch {index} failed after {self._max_attempts} attempts")

    async def batches(self, path: str) -> AsyncIterator[Batch]:
        """Every batch of an endpoint in order, one request at a time."""
        index = 1
        while True:
            batch = await self._get_batch(path, index)
            if batch is None:
                return
            yield batch
            index += 1
            await self._sleep(self._pause)

    async def probe(self, path: str, batch: int = 1) -> tuple[int, dict[str, str], str]:
        """One raw request for diagnosis: status, a few headers, and the start of the body."""
        token = await self._token_value()
        resp = await self._http.get(
            path, params={"batch-number": batch}, headers={"Authorization": f"Bearer {token}"}
        )
        keep = ("content-type", "content-length", "x-cache", "server", "date")
        headers = {k: v for k, v in resp.headers.items() if k.lower() in keep}
        return resp.status_code, headers, resp.text[:2000]

    async def fetch_all(self, path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async for batch in self.batches(path):
            out.extend(batch.records)
        return out

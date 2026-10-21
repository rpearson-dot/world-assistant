"""A hard daily budget for calls to the pollen service.

The provider allows only 40 requests a day on a key ("Too many requests. Maximum 40 requests per day"),
far less than its "every four hours" fair-use text suggests. Every call anywhere (the nationwide grid,
the saved-place history job, and live look-ups) draws from one counter kept in Redis, with the limit set
below 40 so a stray probe or restart cannot push the key over. The day is the UTC date, which is when
such limits normally reset; if the provider resets at another time, only the first calls after it are
affected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.integrations.pollen.client import PollenError
from app.ratelimit import SlidingWindowLimiter


class PollenBudgetError(PollenError):
    """The day's pollen requests are used up. Nothing was sent."""


def _key() -> str:
    return "wa:pollen:daily:" + datetime.now(UTC).date().isoformat()


class PollenLimiter:
    """Drop-in limiter for PollenClient: a per-minute window plus the daily budget."""

    def __init__(self, redis: Any, per_day: int, per_minute: int = 20) -> None:
        self._redis = redis
        self._per_day = per_day
        self._window = SlidingWindowLimiter(redis, "pollen", per_minute)

    async def used(self) -> int:
        try:
            return int(await self._redis.get(_key()) or 0)
        except Exception:  # noqa: BLE001 - if Redis is down, do not block on the count
            return 0

    async def remaining(self) -> int:
        return max(0, self._per_day - await self.used())

    async def acquire(self, max_wait: float = 10.0) -> None:
        n = await self._redis.incr(_key())
        if n == 1:
            await self._redis.expire(_key(), 172800)
        if n > self._per_day:
            await self._redis.decr(_key())  # this call is not made, so it does not count
            raise PollenBudgetError(f"the day's budget of {self._per_day} pollen requests is used up")
        await self._window.acquire(max_wait)

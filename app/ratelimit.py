"""Shared limiter for calls to rate-limited upstream APIs.

National Highways allows 10 calls per subscription key per minute, so every
worker must draw from one budget. This is a sliding-window limiter kept in
Redis and updated atomically by a Lua script, so it holds across processes.
"""

from __future__ import annotations

import asyncio
import time
import uuid

from redis.asyncio import Redis

# Returns 0 when a slot was granted, otherwise the milliseconds to wait.
_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) < limit then
  redis.call('ZADD', key, now, now .. ':' .. ARGV[3])
  redis.call('PEXPIRE', key, window)
  return 0
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local wait = math.ceil(tonumber(oldest[2]) + window - now)
if wait < 1 then wait = 1 end
return wait
"""


class RateLimitExceeded(Exception):
    """Raised when a slot could not be obtained within max_wait seconds."""


class SlidingWindowLimiter:
    def __init__(self, redis: Redis, name: str, limit: int, window_s: float = 60.0) -> None:
        self.key = f"wa:ratelimit:{name}"
        self.limit = limit
        self.window_ms = int(window_s * 1000)
        self._script = redis.register_script(_LUA)

    async def try_acquire(self) -> float:
        """Take a slot if one is free. Returns 0.0 if granted, else seconds to wait."""
        wait_ms = await self._script(
            keys=[self.key], args=[self.window_ms, self.limit, uuid.uuid4().hex]
        )
        return int(wait_ms) / 1000

    async def acquire(self, max_wait: float = 30.0) -> None:
        """Wait for a slot, up to max_wait seconds."""
        deadline = time.monotonic() + max_wait
        while True:
            wait = await self.try_acquire()
            if wait <= 0:
                return
            remaining = deadline - time.monotonic()
            if wait > remaining:
                raise RateLimitExceeded(f"no slot for {self.key} within {max_wait}s")
            await asyncio.sleep(wait + 0.05)

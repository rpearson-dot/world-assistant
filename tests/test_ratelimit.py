"""Limiter tests need a real Redis (they exercise the Lua script).

Set WA_TEST_REDIS_URL, e.g. redis://localhost:6379/15, to run them; otherwise
they are skipped.
"""

import os
import uuid

import pytest
from redis.asyncio import Redis

from app.ratelimit import RateLimitExceeded, SlidingWindowLimiter

REDIS_URL = os.environ.get("WA_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="WA_TEST_REDIS_URL not set")


@pytest.fixture
async def redis():
    r = Redis.from_url(REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


async def test_grants_up_to_limit_then_asks_to_wait(redis):
    limiter = SlidingWindowLimiter(redis, f"test-{uuid.uuid4().hex}", limit=3, window_s=5)
    for _ in range(3):
        assert await limiter.try_acquire() == 0
    wait = await limiter.try_acquire()
    assert 0 < wait <= 5


async def test_acquire_raises_when_wait_exceeds_max(redis):
    limiter = SlidingWindowLimiter(redis, f"test-{uuid.uuid4().hex}", limit=1, window_s=5)
    await limiter.acquire()
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire(max_wait=0.2)


async def test_slot_frees_after_window(redis):
    limiter = SlidingWindowLimiter(redis, f"test-{uuid.uuid4().hex}", limit=1, window_s=1)
    await limiter.acquire()
    await limiter.acquire(max_wait=3)  # waits roughly one second, then succeeds

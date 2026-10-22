"""Show what TfL really sends for road disruptions, and how much of it this app can use.

    docker compose exec worker python -m app.integrations.tflroads.probe

Public information only (no keys or personal data are printed).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter

from redis.asyncio import Redis

from app.config import get_settings
from app.integrations.tfl.client import TflClient, TflError
from app.integrations.tflroads.normalise import parse_item
from app.ratelimit import SlidingWindowLimiter


async def main() -> int:
    s = get_settings()
    redis = Redis.from_url(s.redis_dsn, decode_responses=True)
    try:
        key = s.tfl_app_key
        async with TflClient(key, limiter=SlidingWindowLimiter(redis, "tfl", 250 if key else 30), timeout=60.0) as c:
            data = await c.get("/Road/all/Disruption")
    except TflError as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await redis.aclose()
    items = [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    print(f"{len(items)} disruptions")
    if not items:
        return 0
    print("fields:", ", ".join(f"{k}:{type(v).__name__}" for k, v in items[0].items()))
    parsed = [p for i in items if (p := parse_item(i, s.tfl_naive_tz))]
    print(f"parsed {len(parsed)}; with a position {sum(1 for p in parsed if p['geom'])}; "
          f"lines {sum(1 for p in parsed if p['geom'] and p['geom']['type'] == 'MultiLineString')}")  # fmt: skip
    for name in ("category", "sub_category", "severity", "status", "scope"):
        print(f"{name}:", dict(Counter(p[name] for p in parsed).most_common(8)))
    print("\nfirst record, raw (long text cut):")
    print(json.dumps(items[0], indent=1, default=str)[:3500])
    print("\nfirst record, as parsed:")
    print(json.dumps(parsed[0], indent=1, default=str)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

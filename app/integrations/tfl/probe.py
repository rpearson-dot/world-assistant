"""Look at real TfL responses while setting up.

    python -m app.integrations.tfl.probe search "worcester park"
    python -m app.integrations.tfl.probe departures 910GWORCSPK south-western-railway
    python -m app.integrations.tfl.probe status tube,dlr

Prints field names and a sample record; the data is public timetable information.
"""

from __future__ import annotations

import asyncio
import json
import sys

from redis.asyncio import Redis

from app.config import get_settings
from app.integrations.tfl.client import TflClient, TflError
from app.ratelimit import SlidingWindowLimiter


async def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in {"search", "departures", "status"}:
        print(__doc__)
        return 2
    settings = get_settings()
    redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
    try:
        async with TflClient(settings.tfl_app_key, limiter=SlidingWindowLimiter(redis, "tfl", 30)) as c:
            if argv[0] == "search":
                rows = await c.search_stops(" ".join(argv[1:]))
            elif argv[0] == "departures":
                rows = await c.arrival_departures(argv[1], argv[2].split(",") if len(argv) > 2 else [])
            else:
                rows = await c.line_status(argv[1].split(","))
        print(f"{len(rows)} records")
        if rows:
            print("fields:", ", ".join(f"{k}:{type(v).__name__}" for k, v in rows[0].items()))
            print(json.dumps(rows[0], indent=2, default=str)[:3000])
        return 0
    except TflError as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await redis.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))

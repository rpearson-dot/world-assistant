"""Look at real National Rail responses while setting up.

    python -m app.integrations.rail.probe search "stoneleigh"
    python -m app.integrations.rail.probe departures SNL          # from Stoneleigh
    python -m app.integrations.rail.probe departures SNL WAT      # ... calling at Waterloo

Prints field names and a sample record; the data is public timetable information.
"""

from __future__ import annotations

import asyncio
import json
import sys

from redis.asyncio import Redis

from app.config import get_settings
from app.integrations.rail import make_rail_client
from app.integrations.rail.rdm import RailError
from app.ratelimit import SlidingWindowLimiter


async def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in {"search", "departures"}:
        print(__doc__)
        return 2
    s = get_settings()
    redis = Redis.from_url(s.redis_dsn, decode_responses=True)
    try:
        async with make_rail_client(s, SlidingWindowLimiter(redis, "nr", 30)) as c:
            if argv[0] == "search":
                data: object = await c.search_stations(" ".join(argv[1:]))
            else:
                data = await c.departures(argv[1], 10, argv[2] if len(argv) > 2 else None)
        if isinstance(data, dict):
            print("top-level:", ", ".join(f"{k}:{type(v).__name__}" for k, v in data.items()))
            svcs = next((v for k, v in data.items() if k.lower() == "trainservices"), None) or []
            print(f"{len(svcs)} services")
            if svcs:
                print(
                    "service fields:",
                    ", ".join(f"{k}:{type(v).__name__}" for k, v in svcs[0].items()),
                )
                print(json.dumps(svcs[0], indent=2, default=str)[:3000])
        else:
            print(json.dumps(data, indent=2, default=str)[:3000])
    except RailError as exc:
        print(f"error: {exc}")
        return 1
    finally:
        await redis.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))

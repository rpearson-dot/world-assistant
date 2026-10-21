"""Look at a real Skiddle response while setting up (needs the skiddle_api_key secret).

    python -m app.integrations.skiddle.probe 51.5074 -0.1278 8     # latitude longitude radius_km
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.integrations.skiddle.client import DEFAULT_BASE, SkiddleClient, SkiddleError


async def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    lat, lon = float(argv[0]), float(argv[1])
    radius = float(argv[2]) if len(argv) > 2 else 8.0
    s = get_settings()
    today = datetime.now(ZoneInfo(s.local_tz)).date()
    try:
        async with SkiddleClient(s.skiddle_api_key, s.skiddle_base_url or DEFAULT_BASE) as c:
            # The raw first record, so the real field names are visible.
            raw = await c._get(
                "/events/search/",
                {"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}", "radius": str(max(1, round(radius / 1.609))),
                 "limit": "3", "order": "date", "minDate": today.isoformat(),
                 "maxDate": (today + timedelta(days=14)).isoformat()},
            )
            print("top-level:", ", ".join(f"{k}:{type(v).__name__}" for k, v in raw.items()) if isinstance(raw, dict) else type(raw))
            results = raw.get("results", []) if isinstance(raw, dict) else []
            print(f"{len(results)} events in this sample")
            if results:
                print("event fields:", ", ".join(f"{k}:{type(v).__name__}" for k, v in results[0].items()))
                print(json.dumps(results[0], indent=2, default=str)[:3000])
            parsed = await c.search(lat, lon, radius_km=radius, limit=5)
            print("\nas this app reads them:")
            for e in parsed["events"]:
                print(f"  {e['date']}  {e['name']}  @ {e['venue']}, {e['town']}  {e['price']}  {e['distance_km']} km")
    except (SkiddleError, httpx.HTTPError) as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))

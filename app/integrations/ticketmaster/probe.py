"""Look at a real Ticketmaster response while setting up (needs the ticketmaster_api_key secret).

    python -m app.integrations.ticketmaster.probe 51.5074 -0.1278 8     # latitude longitude radius_km
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

import httpx

from app.config import get_settings
from app.integrations.ticketmaster.client import (
    DEFAULT_BASE,
    TicketmasterClient,
    TicketmasterError,
    geohash,
)


async def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    lat, lon = float(argv[0]), float(argv[1])
    radius = float(argv[2]) if len(argv) > 2 else 8.0
    s = get_settings()
    now = datetime.now(UTC)
    try:
        async with TicketmasterClient(s.ticketmaster_api_key, s.ticketmaster_base_url or DEFAULT_BASE) as c:
            raw = await c._get(
                "/events.json",
                {"geoPoint": geohash(lat, lon), "radius": str(round(radius)), "unit": "km", "size": "3",
                 "sort": "date,asc", "countryCode": "GB",
                 "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "endDateTime": (now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            )
            print("top-level:", ", ".join(f"{k}:{type(v).__name__}" for k, v in raw.items()) if isinstance(raw, dict) else type(raw))
            evs = ((raw.get("_embedded") or {}).get("events") or []) if isinstance(raw, dict) else []
            print(f"{len(evs)} events in this sample; page: {raw.get('page') if isinstance(raw, dict) else None}")
            if evs:
                print("event fields:", ", ".join(f"{k}:{type(v).__name__}" for k, v in evs[0].items()))
                print(json.dumps(evs[0], indent=2, default=str)[:2500])
            parsed = await c.search(lat, lon, radius_km=radius, start=now, end=now + timedelta(days=14), limit=5)
            print("\nas this app reads them:")
            for e in parsed["events"]:
                print(f"  {e['date']} {e['doors_open']}  {e['name']}  @ {e['venue']}, {e['town']}  {e['price']}  {e['distance_km']} km")
    except (TicketmasterError, httpx.HTTPError) as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))

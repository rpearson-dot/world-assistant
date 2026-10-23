"""Check the TfWM feed from this machine, and see what its identifiers look like.

    docker compose exec worker python -m app.integrations.tfwm.probe [stop-id-or-text]

Prints how many trips and vehicles are in the feed, a few example stop ids (to compare with NaPTAN's ATCO
codes), and, when you give a stop id, the next predicted times at it. Nothing is stored.
"""

from __future__ import annotations

import asyncio
import sys
import time

from app.config import get_settings
from app.integrations.tfwm.client import TfwmClient, TfwmError


async def main(argv: list[str]) -> int:
    s = get_settings()
    try:
        client = TfwmClient(s.tfwm_app_id, s.tfwm_app_key, s.tfwm_base_url)
        print("using", "HTTPS" if client.secure else "plain HTTP (the key is readable on the way)")
        stamp, updates = await client.trip_updates()
    except TfwmError as exc:
        print("Failed:", exc)
        return 3
    now = time.time()
    print(f"feed time {stamp} ({round(now - stamp)} s old)" if stamp else "feed has no timestamp")
    vehicles = {u["vehicle"].get("id") or u["vehicle"].get("label") for u in updates} - {"", None}
    print(f"{len(updates)} trip updates, {len(vehicles)} distinct vehicles")
    routes = sorted({u["trip"].get("route_id", "") for u in updates})
    print(f"{len(routes)} route ids, for example", routes[:10])
    ids = [st["stop_id"] for u in updates for st in u["stops"]][:8]
    print("example stop ids (compare with NaPTAN ATCO codes like 43000...):", ids)
    for u in updates[:2]:
        print("  trip", u["trip"], "vehicle", u["vehicle"], "first stops", u["stops"][:2])
    if len(argv) > 1:
        want = argv[1].lower()
        rows = []
        for u in updates:
            for st in u["stops"]:
                if want in st["stop_id"].lower() and (st["arrival"] or {}).get("time"):
                    rows.append((st["arrival"]["time"], u["trip"].get("route_id"), u["vehicle"].get("id"), st["stop_id"]))
        print(f"{len(rows)} predictions matching {argv[1]!r}; next:")
        for t, route, veh, stop in sorted(rows)[:8]:
            print(f"   in {round((t - now) / 60, 1)} min  route {route}  vehicle {veh}  stop {stop}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))

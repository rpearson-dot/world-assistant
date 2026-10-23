"""Show what BODS really sends for a bus line, and what this app makes of it.

    docker compose exec worker python -m app.integrations.bus.bods_probe 213 [lat lon]

It looks for the line's buses round a point (default: your home place), finds the operator, finds and reads
the operator's timetable file, and prints the stops, the weekday times and the countdowns it would give.
Public information only: the key is never printed.
"""

from __future__ import annotations

import asyncio
import sys
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.integrations.bods.client import (
    BodsError,
    box_around,
    fetch_vehicles,
    find_datasets,
    operators_of_line,
)
from app.integrations.bus import bods_source, store


async def main(line: str, lat: float | None, lon: float | None) -> int:
    s = get_settings()
    if not s.bods_api_key:
        print("No bods_api_key set (Settings > Keys).")
        return 2
    engine = create_async_engine(s.database_url)
    try:
        if lat is None or lon is None:
            async with engine.connect() as conn:
                row = (await conn.execute(text(
                    "SELECT lat, lon FROM place ORDER BY (kind = 'home') DESC, (id = 'home') DESC LIMIT 1"))).first()
            if row is None:
                print("No home place saved: give a latitude and longitude after the line.")
                return 2
            lat, lon = row
        box = box_around(lat, lon, 15.0)
        vehicles = [v for v in await fetch_vehicles(s.bods_api_key, box) if (v.get("line") or "").upper() == line.upper()]
        print(f"{len(vehicles)} buses on line {line} within 15 km of {lat:.4f},{lon:.4f}")
        for v in vehicles[:3]:
            print("  ", {k: v.get(k) for k in ("vehicle", "operator", "destination", "direction", "bearing", "recorded_at")})
        nocs = await operators_of_line(s.bods_api_key, line, box)
        print("operators:", nocs or "none seen (no bus running now?)")
        for n in nocs[:2]:
            sets = await find_datasets(s.bods_api_key, noc=n)
            print(f"timetable datasets for {n}: {len(sets)}", [d['name'][:40] for d in sets[:4]])

        async def coords_of(sids: list[str]) -> dict[str, tuple[float, float]]:
            async with engine.connect() as c:
                return await store.stop_coords(c, sids)

        route, noc = await bods_source.load_route(s.bods_api_key, line, nocs[0] if nocs else None, box,
                                                  bods_source.near_check(coords_of, box))
        seq = route.sequences()
        for d, stops in seq.items():
            print(f"{d}: {len(stops)} stops", (stops[0]["name"], "...", stops[-1]["name"]) if stops else "")
        print(f"{len(route.patterns)} patterns, {len(route.journeys)} weekday journeys, operator {noc}")
        first = seq["outbound"][0]["id"] if seq["outbound"] else None
        if first:
            print("weekday times from the first outbound stop:", route.times_at(first, "outbound")[:12])
        async with engine.connect() as conn:
            sids = [x["id"] for stops in seq.values() for x in stops]
            coords = await store.stop_coords(conn, sids)
        print(f"{len(coords)} of {len(set(sids))} stops are in the national stop register (NaPTAN)")
        routes = {d: [bods_source.Stop(x["id"], x["name"], *coords[x["id"]]) for x in stops if x["id"] in coords]
                  for d, stops in seq.items()}  # fmt: skip
        preds = bods_source.arrivals(vehicles, routes, line, time.time())
        print(f"{len(preds)} countdowns, for example:")
        for p in sorted(preds, key=lambda p: p["timeToStation"])[:5]:
            print("  ", p["vehicleId"], p["stationName"], f"{p['timeToStation'] // 60} min")
    except BodsError as exc:
        print("Failed:", exc)
        return 3
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(args[0], float(args[1]) if len(args) > 2 else None, float(args[2]) if len(args) > 2 else None)))

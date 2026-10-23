"""Show which authorities publish bus disruptions to BODS, and which disruptions are near you.

    docker compose exec worker python -m app.integrations.bods.disruptions_probe [lat lon] [km]

Default: round your home place, 25 km. A disruption counts as near when one of its stops (its own position, or
the national stop register's) or a place it names is within that distance. Nothing is stored; the key is never
printed.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.integrations.bods.client import BodsError
from app.integrations.bods.disruptions import fetch, km, parse_situations
from app.integrations.bus import store


async def main(lat: float | None, lon: float | None, radius: float) -> int:
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
                print("No home place saved: give a latitude and longitude.")
                return 2
            lat, lon = float(row[0]), float(row[1])
        url, body, tries = await fetch(s.bods_api_key)
        for t in tries:
            print("tried", t)
        print(f"read {url} ({len(body)} bytes)")
        found = parse_situations(body)
        print(f"{len(found)} disruptions")
        by = Counter(x.publisher or "(no publisher given)" for x in found)
        print(f"{len(by)} publishers:")
        for name, n in by.most_common():
            print(f"   {n:4}  {name}")
        sids = sorted({sid for x in found for sid in x.stops})
        async with engine.connect() as conn:
            coords = await store.stop_coords(conn, sids)
        print(f"{len(coords)} of {len(sids)} named stops are in the national stop register (NaPTAN)")
        prefixes = Counter(sid[:3] for sid in sids)
        print("stop code areas (first three digits of the ATCO code):", dict(prefixes.most_common(15)))
        here = (lat, lon)
        near = []
        for x in found:
            pts = x.places + [coords[sid] for sid in x.stops if sid in coords]
            if pts and min(km(here, p) for p in pts) <= radius:
                near.append((min(km(here, p) for p in pts), x))
        print(f"{len(near)} within {radius:g} km of {lat:.4f},{lon:.4f}:")
        for d, x in sorted(near, key=lambda t: t[0])[:15]:
            print(f"   {d:5.1f} km", x.brief())
        unplaced = [x for x in found if not x.places and not any(sid in coords for sid in x.stops)]
        if unplaced:
            print(f"{len(unplaced)} could not be placed (no stops or places we know), for example:")
            for x in unplaced[:3]:
                print("  ", x.brief())
    except BodsError as exc:
        print("Failed:", exc)
        return 1
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        la = float(args[0]) if len(args) >= 2 else None
        lo = float(args[1]) if len(args) >= 2 else None
        r = float(args[2]) if len(args) >= 3 else (float(args[0]) if len(args) == 1 else 25.0)
    except ValueError:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(la, lo, r)))

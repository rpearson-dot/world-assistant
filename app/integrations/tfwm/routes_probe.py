"""Find which of TfWM's route ids is a bus line (TfWM has no route list on its portal).

    docker compose exec worker python -m app.integrations.tfwm.routes_probe 907 [lat lon]

Tries, and reports each: TfWM's GTFS timetable, BODS's West Midlands GTFS (same company), and matching the
line's stops (from its BODS timetable, near lat lon or your home) against the stops TfWM's live trips call at.
Nothing is stored; keys are never printed.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import zipfile

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.integrations.bods.client import BodsError, box_around
from app.integrations.bus import bods_source, store
from app.integrations.tfwm.client import TfwmClient, TfwmError, stream_to
from app.integrations.tfwm.routes import match_by_stops, read_routes

BODS_GTFS = "https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/west_midlands/"


async def _gtfs(name: str, fetch, live: set[str], line: str) -> bool:  # type: ignore[no-untyped-def]
    print(f"\n{name}:")
    with tempfile.TemporaryFile() as tmp:
        try:
            size = await fetch(tmp)
        except TfwmError as exc:
            print("   not available:", exc)
            return False
        tmp.seek(0)
        try:
            routes = read_routes(tmp)
        except zipfile.BadZipFile:
            tmp.seek(0)
            print(f"   {size} bytes, not a zip; starts {tmp.read(80)!r}")
            return False
    known = live & routes.keys()
    print(f"   {size // 1024} KB, {len(routes)} routes; {len(known)} of the {len(live)} live route ids are in it")
    for rid in sorted(known)[:5]:
        r = routes[rid]
        print(f"   e.g. {rid} = {r.short_name or '?'} ({r.agency}) {r.long_name[:50]}")
    mine = [r for r in routes.values() if r.short_name.upper() == line.upper()]
    for r in mine:
        print(f"   line {line}: route id {r.route_id} ({r.agency}){' - running now' if r.route_id in live else ''}")
    if not mine:
        print(f"   no route named {line}")
    return bool(known)


async def main(line: str, lat: float | None, lon: float | None) -> int:
    s = get_settings()
    try:
        client = TfwmClient(s.tfwm_app_id, s.tfwm_app_key, s.tfwm_base_url)
        _, updates = await client.trip_updates()
    except TfwmError as exc:
        print("Failed:", exc)
        return 3
    live = {u["trip"].get("route_id", "") for u in updates} - {""}
    print(f"TfWM live feed: {len(updates)} trips on {len(live)} route ids")

    found = await _gtfs("1. TfWM's GTFS timetable (/gtfs/tfwm_gtfs.zip)",
                        lambda out: client.download("/gtfs/tfwm_gtfs.zip", out), live, line)
    if s.bods_api_key:
        found |= await _gtfs("2. BODS GTFS, West Midlands",
                             lambda out: stream_to(BODS_GTFS, out, {"api_key": s.bods_api_key}), live, line)
    else:
        print("\n2. BODS GTFS: skipped, no bods_api_key")

    print(f"\n3. Matching line {line}'s stops (BODS timetable) with TfWM's live trips:")
    engine = create_async_engine(s.database_url)
    try:
        if lat is None or lon is None:
            async with engine.connect() as conn:
                row = (await conn.execute(text(
                    "SELECT lat, lon FROM place ORDER BY (kind = 'home') DESC, (id = 'home') DESC LIMIT 1"))).first()
            if row is None:
                print("   no home place saved: give a latitude and longitude after the line")
                return 0 if found else 1
            lat, lon = float(row[0]), float(row[1])
        box = box_around(lat, lon, 15.0)

        async def coords_of(sids: list[str]) -> dict[str, tuple[float, float]]:
            async with engine.connect() as c:
                return await store.stop_coords(c, sids)

        route, noc = await bods_source.load_route(s.bods_api_key, line, None, box, bods_source.near_check(coords_of, box))
        stops = {x["id"] for seq in route.sequences().values() for x in seq}
        print(f"   BODS timetable for {line} ({noc}): {len(stops)} stops")
        best = match_by_stops(updates, stops)
        for rid, share, shared in best:
            print(f"   route id {rid}: {shared} stops shared, {share:.0%} of its stops are on line {line}")
        if not best:
            print("   no live TfWM trip calls at these stops now (no bus running, or a different stop code scheme)")
        found |= bool(best and best[0][1] >= 0.8)
    except BodsError as exc:
        print("   could not load the line from BODS:", exc)
    finally:
        await engine.dispose()
    return 0 if found else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    try:
        la, lo = (float(args[1]), float(args[2])) if len(args) >= 3 else (None, None)
    except ValueError:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(args[0], la, lo)))

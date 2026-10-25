"""Show what the UK Power Networks portal has for each dataset this app uses, and what this app makes of it.

    docker compose exec worker python -m app.integrations.ukpn.probe [lat lon]

For each: the dataset id found, its fields (name and type), a sample record as read, and for the area datasets
which area the point (default: your home) is in. Nothing is stored; the key is never printed.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.integrations.ukpn import normalise as N
from app.integrations.ukpn.client import TITLES, UkpnClient, UkpnError, compact

AREAS = ("boundary", "idno", "gsp", "primary", "secondary", "rota")


async def main(lat: float | None, lon: float | None) -> int:
    s = get_settings()
    if lat is None or lon is None:
        engine = create_async_engine(s.database_url)
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT lat, lon FROM place ORDER BY (kind = 'home') DESC, (id = 'home') DESC LIMIT 1"))).first()
        await engine.dispose()
        if row is None:
            print("No home place saved: give a latitude and longitude.")
            return 2
        lat, lon = float(row[0]), float(row[1])
    print(f"key: {'set' if s.ukpn_api_key else 'not set (some datasets may refuse)'}; point {lat:.4f},{lon:.4f}")
    now = datetime.now(UTC)
    async with UkpnClient(s.ukpn_api_key) as c:
        for kind in TITLES:
            print(f"\n== {kind}")
            try:
                ds = await c.dataset(kind)
            except UkpnError as exc:
                print("   not found:", exc)
                continue
            print(f"   id {ds.id}: {ds.title}")
            print("   fields:", ", ".join(f"{f.get('name')}:{f.get('type')}" for f in ds.fields)[:900])
            try:
                if kind == "rota":
                    print("   published as a document, not map data: a place cannot be looked up in it")
                    continue
                if kind in AREAS:
                    recs = await c.at_point(ds, lat, lon)
                    print(f"   at the point: {len(recs)}", [N.area(r, kind) for r in recs[:3]])
                    if recs:
                        print("   first, as published:", str(compact(recs[0]))[:600])
                    continue
                recs = await c.export(ds, limit=3)
            except UkpnError as exc:
                print("   could not read:", exc)
                continue
            if not recs:
                print("   no records")
                continue
            print("   first, as published:", str(compact(recs[0]))[:700])
            r = recs[0]
            if kind == "faults":
                f = N.fault(r, now)
                print("   read as:", {k: f[k] for k in ("id", "planned", "status", "active", "postcodes", "customers",
                                                        "started_at", "restore_at", "ended_at", "lat", "lon")})  # fmt: skip
                live = [N.fault(x, now) for x in await c.export(ds)]
                print(f"   in the feed now: {len(live)}, still in progress: {sum(x['active'] for x in live)}")
            elif kind.startswith("works"):
                w = N.works(r, kind.split("_")[1], now)
                print("   read as:", {k: w[k] for k in ("id", "street", "town", "usrn", "category", "traffic",
                                                        "starts_at", "ends_at", "lat", "lon")})  # fmt: skip
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        la, lo = (float(a[0]), float(a[1])) if len(a) >= 2 else (None, None)
    except ValueError:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(la, lo)))

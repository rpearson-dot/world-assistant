"""What GRIDSERVE's API sends, and how this app reads it. Prints no key.

    docker compose exec worker python -m app.integrations.gridserve.probe
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter

from app.config import get_settings
from app.integrations.gridserve import normalise as N
from app.integrations.gridserve.client import GridserveClient, GridserveError


async def main() -> int:
    key = get_settings().gridserve_api_key
    if not key:
        print("No key: save it as gridserve_api_key (Settings > Keys).")
        return 2
    try:
        async with GridserveClient(key) as c:
            locs = await c.locations()
            tariffs = await c.tariffs()
    except GridserveError as exc:
        print("Could not read:", exc)
        return 3
    raw = Counter(str(e.get("status")) for x in locs for e in x.get("evses") or [])
    got = [s for s in (N.site(x) for x in locs) if s]
    print(f"locations: {len(locs)} listed, {len(got)} published with a position")
    print("charger status as sent:", dict(raw.most_common()))
    print("as read:", dict(Counter(c["status"] for _, cs in got for c in cs).most_common()))
    print("connectors:", dict(Counter(x["type"] for _, cs in got for c in cs for x in c["connectors"]).most_common()))
    prices = {t["id"]: t["pence_per_kwh"] for t in (N.tariff(x) for x in tariffs) if t}
    print("tariffs (p/kWh with VAT):", prices)
    if got:
        row, chargers = max(got, key=lambda g: len(g[1]))
        print("\nthe biggest site, as read:")
        print(json.dumps({k: row[k] for k in ("id", "name", "network", "postcode", "lat", "lon", "open_24h")}, default=str))
        short = {k.removeprefix(f"{N.OPERATOR}:"): v for k, v in prices.items() if v is not None}
        print(N.sentence(row["name"], N.summary(chargers, short)))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

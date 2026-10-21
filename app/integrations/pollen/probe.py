"""Look at a real pollen response while setting up (needs the pollen_api_key secret).

    python -m app.integrations.pollen.probe 51.5074 -0.1278      # latitude longitude
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.integrations.pollen.client import DEFAULT_BASE, PollenClient, PollenError, parse_forecast


async def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    lat, lon = float(argv[0]), float(argv[1])
    s = get_settings()
    today = datetime.now(ZoneInfo(s.local_tz)).date()
    try:
        async with PollenClient(s.pollen_api_key, s.pollen_base_url or DEFAULT_BASE) as c:
            raw = await c.raw(lat, lon, country=s.pollen_country, lang="en")
        if isinstance(raw, dict):
            print("top-level:", ", ".join(f"{k}:{type(v).__name__}" for k, v in raw.items()))
            allergens = raw.get("contamination") or []
            print(f"{len(allergens)} allergens")
            if allergens and isinstance(allergens[0], dict):
                print("allergen fields:", ", ".join(f"{k}:{type(v).__name__}" for k, v in allergens[0].items()))
                print(json.dumps(allergens[0], indent=2, default=str)[:1500])
            for k in ("allergyrisk", "allergyrisk_hourly"):
                print(k, "=", json.dumps(raw.get(k), default=str)[:400])
        f = parse_forecast(raw, today)
        print("\nas this app reads them:")
        for a in f["allergens"]:
            print(f"  {a['name']:<20} {a['labels']}")
        print("  allergy risk", f["allergy_risk"], f["allergy_risk_label"])
    except (PollenError, httpx.HTTPError) as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))

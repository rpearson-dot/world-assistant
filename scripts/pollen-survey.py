"""One-off survey: how finely does the pollen provider really vary across Britain?

Run from the project folder on the server (about 100 calls, 3 seconds apart, about 5 minutes):

    docker compose exec -T api python - < scripts/pollen-survey.py

It asks for the forecast along two short lines of points (to see how close two points have to be
before the answer changes) and on a 1 degree grid over Britain (to count how many different
answers there are). It prints a summary and writes nothing. It uses the pollen_api_key secret
that the api container already has, and sends no coordinates of yours.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from app.config import get_settings
from app.integrations.pollen.client import DEFAULT_BASE, PollenClient

PAUSE_S = 3.0


def fingerprint(raw: Any) -> str:
    """The same forecast gives the same fingerprint: allergens, daily risk and hourly risk."""
    if not isinstance(raw, dict):
        return "error"
    keep = {k: raw.get(k) for k in ("contamination", "allergyrisk", "allergyrisk_hourly")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:6]


def transect_points() -> dict[str, list[tuple[float, float]]]:
    west_east = [(52.0, round(-2.0 + 0.1 * i, 1)) for i in range(21)]  # 20 km steps ~ 0.1 degrees
    south_north = [(round(51.0 + 0.1 * i, 1), -1.0) for i in range(21)]
    return {"west to east along 52.0N, every 0.1 degrees": west_east,
            "south to north along 1.0W, every 0.1 degrees": south_north}  # fmt: skip


def grid_points() -> list[tuple[float, float]]:
    return [(float(lat), float(lon)) for lat in range(50, 59) for lon in range(-6, 2)]


def runs(prints: list[tuple[tuple[float, float], str]]) -> list[tuple[tuple[float, float], tuple[float, float], str, int]]:
    """Consecutive points with the same answer, as (first, last, fingerprint, count)."""
    out: list[tuple[tuple[float, float], tuple[float, float], str, int]] = []
    for pt, fp in prints:
        if out and out[-1][2] == fp:
            f, _, _, n = out[-1]
            out[-1] = (f, pt, fp, n + 1)
        else:
            out.append((pt, pt, fp, 1))
    return out


async def survey(raw_for: Any, pause: float = PAUSE_S) -> dict[str, Any]:
    result: dict[str, Any] = {"transects": {}, "extra_fields": {}}

    async def get(pt: tuple[float, float]) -> Any:
        await asyncio.sleep(pause)
        try:
            return await raw_for(pt[0], pt[1])
        except Exception as exc:  # noqa: BLE001 - report and carry on
            return {"error": type(exc).__name__}

    first = True
    for name, pts in transect_points().items():
        prints = []
        for pt in pts:
            raw = await get(pt)
            if first and isinstance(raw, dict):
                result["extra_fields"] = {k: v for k, v in raw.items()
                                          if k not in ("contamination", "allergyrisk", "allergyrisk_hourly")
                                          and isinstance(v, (str, int, float, bool))}  # fmt: skip
                result["top_level_keys"] = sorted(raw)
                first = False
            prints.append((pt, fingerprint(raw)))
        result["transects"][name] = runs(prints)
    grid = [(pt, fingerprint(await get(pt))) for pt in grid_points()]
    groups: dict[str, list[tuple[float, float]]] = {}
    for pt, fp in grid:
        groups.setdefault(fp, []).append(pt)
    result["grid_points"] = len(grid)
    result["grid_distinct"] = len(groups)
    result["grid_groups"] = sorted(groups.values(), key=len, reverse=True)
    return result


def show(result: dict[str, Any]) -> None:
    print("top-level fields in a response:", result.get("top_level_keys"))
    print("other simple fields (could name the location):", result.get("extra_fields") or "none")
    for name, rs in result["transects"].items():
        print(f"\n{name}: {len(rs)} different answers")
        for a, b, fp, n in rs:
            print(f"  {fp}  {a} to {b}  ({n} points)")
    print(f"\n1 degree grid over Britain: {result['grid_points']} points, {result['grid_distinct']} different answers")
    for g in result["grid_groups"][:12]:
        print(f"  {len(g):3d} points like {g[0]} ... {g[-1]}")


async def _main() -> None:
    s = get_settings()
    async with PollenClient(s.pollen_api_key, s.pollen_base_url or DEFAULT_BASE) as c:
        async def raw_for(lat: float, lon: float) -> Any:
            return await c.raw(lat, lon, country=s.pollen_country, lang="en")

        show(await survey(raw_for))


if __name__ == "__main__":
    asyncio.run(_main())

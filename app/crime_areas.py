"""Crime in the borough around a point compared with the boroughs next to it: recorded crimes over the latest three
published months (data.police.uk, counted inside each borough's boundary), a year's worth per 1,000 residents (ONS
mid-year population estimates, through Nomis), and per square kilometre where the population cannot be had.

The boroughs (districts and unitary authorities) and their boundaries are Ordnance Survey Boundary-Line, already kept by
World Assistant (`boundary`, kind `district`). Neighbours are the districts touching the point's own. A borough's count
for a month is kept, since published months do not change; a population for a year.

    python -m app.crime_areas LAT LON     the live check's probe: the boroughs, their counts and populations
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import crime as C

log = logging.getLogger(__name__)
NOMIS = "https://www.nomisweb.co.uk/api/v01/dataset/NM_2002_1.data.json"  # population estimates, local authority based
POP_ATTRIBUTION = "ONS mid-year population estimates (Nomis), Open Government Licence v3.0"
MONTHS = 1  # the latest published month (a London borough's month is a few thousand crimes, a megabyte or two)
MOST = 6  # neighbours compared
POINTS = 90  # a boundary as at most this many points for data.police.uk
_counts: dict[tuple[str, str], tuple[float, int | None]] = {}
_pops: dict[str, tuple[float, dict[str, Any]]] = {}
YEAR_S = 365 * 86400

NEAR = """
WITH me AS (SELECT id, geom FROM boundary WHERE kind = 'district' AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
            ORDER BY ST_Area(geom) LIMIT 1)
SELECT b.name, b.code, ST_Area(b.geom::geography) / 1e6 AS km2, b.id = me.id AS here,
       ST_AsGeoJSON(ST_SimplifyPreserveTopology(b.geom, 0.002)) AS shape
FROM boundary b, me
WHERE b.kind = 'district' AND (b.id = me.id OR ST_Intersects(b.geom, ST_Buffer(me.geom, 0.003)))
ORDER BY (b.id = me.id) DESC, ST_Distance(ST_Centroid(b.geom), ST_Centroid(me.geom))
LIMIT :n
"""


async def boroughs(engine: AsyncEngine, lat: float, lon: float) -> list[dict[str, Any]]:
    """The point's borough first, then those touching it (nearest first)."""
    async with engine.connect() as c:
        rows = (await c.execute(text(NEAR), {"lat": lat, "lon": lon, "n": MOST + 1})).all()
    return [{"name": r.name, "code": r.code, "km2": round(float(r.km2), 1), "here": bool(r.here), "shape": json.loads(r.shape)} for r in rows]


def poly(shape: dict[str, Any]) -> str:
    """The borough's outline (its largest part) as data.police.uk's poly: lat,lng:lat,lng:..., at most POINTS points."""
    rings = [shape["coordinates"][0]] if shape["type"] == "Polygon" else [p[0] for p in shape["coordinates"]]
    ring = max(rings, key=len)
    if len(ring) > POINTS:
        step = (len(ring) - 1) / (POINTS - 1)
        ring = [ring[round(i * step)] for i in range(POINTS)]
    return ":".join(f"{y:.5f},{x:.5f}" for x, y in ring)


async def count(client: httpx.AsyncClient, code: str, shape: dict[str, Any], month: str) -> int | None:
    """Crimes recorded inside a borough in a month (None: too many for one request, or not answered)."""
    key = (code, month)
    hit = _counts.get(key)
    if hit and (hit[1] is not None or time.time() - hit[0] < 3600):
        return hit[1]
    await C._turn()
    try:
        r = await client.post(C.BASE + "/crimes-street/all-crime", data={"poly": poly(shape), "date": month},
                               headers={"Accept": "application/json", "User-Agent": C._ua()}, timeout=60)  # fmt: skip
    except httpx.HTTPError as exc:
        raise C.CrimeError(f"data.police.uk could not be reached ({type(exc).__name__})") from exc
    if r.status_code == 503:  # more than 10,000 crimes: the API will not count that many in one go
        n = None
    elif r.status_code == 429:
        raise C.CrimeError("data.police.uk says too many requests: try again in a moment")
    elif r.status_code >= 400:
        raise C.CrimeError(f"data.police.uk answered HTTP {r.status_code}")
    else:
        n = len(r.json() or [])
    _counts[key] = (time.time(), n)
    return n


async def populations(client: httpx.AsyncClient, codes: list[str]) -> dict[str, dict[str, Any]]:
    """The latest mid-year population of each borough (by its ONS code), from Nomis; those it does not answer for are
    left out."""
    out = {c: v for c in codes if (h := _pops.get(c)) and time.time() - h[0] < YEAR_S for v in [h[1]]}
    todo = [c for c in codes if c not in out]
    if not todo:
        return out
    try:
        r = await client.get(NOMIS, params={"geography": ",".join(todo), "date": "latest", "gender": 0, "c_age": 200, "measures": 20100,
                                            "select": "geography_code,geography_name,date_name,obs_value"}, timeout=30)  # fmt: skip
        r.raise_for_status()
        for o in (r.json() or {}).get("obs") or []:
            code = ((o.get("geography") or {}).get("geogcode")) or ((o.get("geography") or {}).get("value"))
            val = (o.get("obs_value") or {}).get("value")
            if code and isinstance(val, int | float):
                v = {"population": int(val), "year": (o.get("date") or {}).get("description")}
                _pops[str(code)] = (time.time(), v)
                out[str(code)] = v
    except (httpx.HTTPError, ValueError) as exc:
        log.info("borough populations not read: %s", exc)
    return out


async def compare(engine: AsyncEngine, lat: float, lon: float, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """The point's borough and its neighbours: crimes a year per 1,000 residents (or per km²), lowest first, and where
    the point's borough comes. None where there are no boroughs (Boundary-Line not loaded, or outside Britain)."""
    try:
        areas = await boroughs(engine, lat, lon)
    except Exception:
        log.info("no boroughs to compare", exc_info=True)
        return None
    if not areas or not areas[0]["here"]:
        return None
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        last = await C.latest_month(client)
        months = C.months_back(last, MONTHS)
        pops = await populations(client, [a["code"] for a in areas if a.get("code")])
        for a in areas:
            got = [await count(client, a["code"] or a["name"], a["shape"], m) for m in months]
            a["crimes"] = None if any(n is None for n in got) else sum(got)  # type: ignore[misc]
    finally:
        if own:
            await client.aclose()
    for a in areas:
        a.pop("shape", None)
        p = (pops.get(a.get("code") or "") or {}).get("population")
        a["population"] = p
        year = a["crimes"] * 12 / MONTHS if a["crimes"] is not None else None
        a["per_1000"] = round(year / p * 1000, 1) if year is not None and p else None
        a["per_km2"] = round(year / a["km2"], 1) if year is not None and a["km2"] else None
    by = "per_1000" if all(a["per_1000"] is not None for a in areas if a["crimes"] is not None) and areas[0]["per_1000"] is not None else "per_km2"
    ranked = sorted([a for a in areas if a.get(by) is not None], key=lambda a: a[by])
    home = areas[0]
    return {"borough": home["name"], "by": by, "months": months, "boroughs": ranked,
            "rank": next((i + 1 for i, a in enumerate(ranked) if a["here"]), None), "of": len(ranked),
            "source": C.ATTRIBUTION + ("; " + POP_ATTRIBUTION if by == "per_1000" else "")}  # fmt: skip


def words(cmp: dict[str, Any] | None) -> str:
    """One line: where the borough comes among its neighbours."""
    if not cmp or not cmp.get("rank"):
        return ""
    home = next(a for a in cmp["boroughs"] if a["here"])
    unit = "a year per 1,000 residents" if cmp["by"] == "per_1000" else "a year per km²"
    r, n = cmp["rank"], cmp["of"]
    where = "the lowest" if r == 1 else "the highest" if r == n else f"{_nth(r)} lowest"
    lo, hi = cmp["boroughs"][0], cmp["boroughs"][-1]
    return (f"{home['name']}: about {home[cmp['by']]:g} crimes {unit}, {where} of {n} neighbouring boroughs"
            f" (lowest {lo['name']} {lo[cmp['by']]:g}, highest {hi['name']} {hi[cmp['by']]:g}).")


def _nth(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


if __name__ == "__main__":  # the live check's probe
    import sys

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import get_settings

    async def _main(lat: float, lon: float) -> None:
        eng = create_async_engine(get_settings().database_url)
        try:
            got = await compare(eng, lat, lon)
        finally:
            await eng.dispose()
        if not got:
            print("No borough comparison (Boundary-Line districts not loaded, or no borough here).")
            return
        print(words(got))
        for a in got["boroughs"]:
            print(f"  {a['name']} ({a['code']}): {a['crimes']} crimes in {MONTHS} months, population {a['population']}, "
                  f"{a['per_1000']} per 1,000, {a['per_km2']} per km²")  # fmt: skip
        print("Population for every borough:" if all(a["population"] for a in got["boroughs"]) else "Some populations missing (Nomis).")

    asyncio.run(_main(float(sys.argv[1]), float(sys.argv[2])))

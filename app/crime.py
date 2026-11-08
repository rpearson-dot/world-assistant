"""Crime and the local police team for a place, from data.police.uk (no key; Open Government Licence v3.0).

Looked up when asked, not collected: street-level crimes within a mile of a point (the latest month the police have
published is about two months behind; by default the twelve months up to it, added up), what happened to them, and the
neighbourhood policing team for the point with its contact details, priorities and coming events. England, Wales and
Northern Ireland; in Scotland only the British Transport Police publish, so crime there looks far lower than it is.

    GET /v1/crime?lat=&lon=&months=12    (or postcode=) the summary over the months, and each month's count

Answers are kept in memory: a month's crimes for a point for a day (they do not change once published), the
neighbourhood for a week. The API allows 15 requests a second on average; a 12-month trend is 12 requests, at most 4
at a time.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import require_token

log = logging.getLogger("wa.crime")
router = APIRouter(prefix="/v1/crime", tags=["crime"], dependencies=[Depends(require_token)])
BASE = "https://data.police.uk/api"
ATTRIBUTION = "Contains public sector information from data.police.uk, licensed under the Open Government Licence v3.0"
DAY, WEEK = 86400, 7 * 86400
_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}

# outcome categories are many and wordy: grouped for the summary
OUTCOME = (("under investigation|awaiting court", "still open"),
           ("no suspect|unable to prosecute|not in the public interest|no further action|formal action", "no one dealt with"),
           (("charged|summonsed|cautioned|caution|penalty|fined|offender sent|court|community resolution|drugs possession "
             "warning|offender given|offender ordered|offender deprived|offender otherwise|conditional discharge|"
             "suspended|local resolution"), "someone dealt with"),
           ("transferred|another", "passed on"))  # fmt: skip


class CrimeError(Exception):
    pass


def _fresh(key: tuple[Any, ...], ttl: float) -> Any:
    got = _cache.get(key)
    return got[1] if got and time.time() - got[0] < ttl else None


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        r = await client.get(BASE + path, params=params, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise CrimeError(f"data.police.uk could not be reached ({type(exc).__name__})") from exc
    if r.status_code == 404:
        return None
    if r.status_code == 429:
        raise CrimeError("data.police.uk says too many requests: try again in a moment")
    if r.status_code >= 400:
        raise CrimeError(f"data.police.uk answered HTTP {r.status_code}")
    return r.json() if r.content else None


async def latest_month(client: httpx.AsyncClient) -> str:
    """The latest month with published crimes, as YYYY-MM."""
    got = _fresh(("last",), 6 * 3600)
    if got:
        return got  # type: ignore[no-any-return]
    d = await _get(client, "/crime-last-updated")
    month = str((d or {}).get("date") or "")[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise CrimeError("data.police.uk did not say which month is latest")
    _cache[("last",)] = (time.time(), month)
    return month


def months_back(month: str, n: int) -> list[str]:
    """month and the n-1 before it, oldest first."""
    y, m = int(month[:4]), int(month[5:7])
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


async def categories(client: httpx.AsyncClient) -> dict[str, str]:
    got = _fresh(("cats",), WEEK)
    if got:
        return got  # type: ignore[no-any-return]
    d = await _get(client, "/crime-categories") or []
    names = {c["url"]: c["name"] for c in d if isinstance(c, dict) and c.get("url")}
    _cache[("cats",)] = (time.time(), names)
    return names


async def crimes(client: httpx.AsyncClient, lat: float, lon: float, month: str) -> list[dict[str, Any]]:
    key = ("crimes", round(lat, 4), round(lon, 4), month)
    got = _fresh(key, DAY)
    if got is not None:
        return got  # type: ignore[no-any-return]
    d = await _get(client, "/crimes-street/all-crime", {"lat": f"{lat:.6f}", "lng": f"{lon:.6f}", "date": month})
    rows = d if isinstance(d, list) else []
    _cache[key] = (time.time(), rows)
    return rows


def outcome_group(o: dict[str, Any] | None) -> str:
    cat = str((o or {}).get("category") or "").lower()
    if not cat:
        return "not recorded"
    for pattern, group in OUTCOME:
        if re.search(pattern, cat):
            return group
    return "other"


def summarise(rows: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any]:
    by_cat = Counter(names.get(r.get("category"), str(r.get("category") or "other").replace("-", " ").capitalize()) for r in rows)
    streets = Counter(str(((r.get("location") or {}).get("street") or {}).get("name") or "") for r in rows)
    streets.pop("", None)
    outcomes = Counter(outcome_group(r.get("outcome_status")) for r in rows)
    return {"total": len(rows), "by_category": [{"name": k, "count": n} for k, n in by_cat.most_common()],
            "streets": [{"name": k, "count": n} for k, n in streets.most_common(5)],
            "outcomes": dict(outcomes.most_common())}  # fmt: skip


def _plain(s: Any) -> str:
    """Priorities and events come as HTML: as plain text."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()


async def neighbourhood(client: httpx.AsyncClient, lat: float, lon: float, today: date | None = None) -> dict[str, Any] | None:
    key = ("nbhd", round(lat, 3), round(lon, 3))
    got = _fresh(key, WEEK)
    if got is not None:
        return got or None
    loc = await _get(client, "/locate-neighbourhood", {"q": f"{lat:.6f},{lon:.6f}"})
    if not loc or not loc.get("force"):
        _cache[key] = (time.time(), {})
        return None
    force, nb = loc["force"], loc["neighbourhood"]
    info, pri, events, force_info = await asyncio.gather(
        _get(client, f"/{force}/{nb}"), _get(client, f"/{force}/{nb}/priorities"), _get(client, f"/{force}/{nb}/events"),
        _get(client, f"/forces/{force}"))  # fmt: skip
    info = info or {}
    contact = {k: v for k, v in (info.get("contact_details") or {}).items() if k in ("telephone", "email", "web", "twitter") and v}
    today = today or datetime.now(UTC).date()
    coming = []
    for e in events or []:
        start = str(e.get("start_date") or "")
        if start[:10] >= today.isoformat():
            coming.append({"title": _plain(e.get("title")), "start": start, "address": _plain(e.get("address")) or None,
                           "description": _plain(e.get("description"))[:300] or None})  # fmt: skip
    coming.sort(key=lambda e: e["start"])
    pri = sorted(pri or [], key=lambda p: str(p.get("issue-date") or ""), reverse=True)
    out = {"force": force, "force_name": (force_info or {}).get("name") or force, "id": nb,
           "name": _plain(info.get("name")) or nb, "url": info.get("url_force"), "contact": contact,
           "priorities": [{"issue": _plain(p.get("issue")), "since": str(p.get("issue-date") or "")[:10] or None,
                           "action": _plain(p.get("action")) or None} for p in pri[:3]],
           "events": coming[:3],
           "stations": [{"name": x.get("name"), "address": x.get("address"), "postcode": x.get("postcode")}
                        for x in (info.get("locations") or []) if x.get("type") == "station"][:2]}  # fmt: skip
    _cache[key] = (time.time(), out)
    return out


async def look_up(lat: float, lon: float, months: int = 12, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """The months up to the latest published, added up: what kinds of crime, where, and what happened to them."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        last = await latest_month(client)
        names = await categories(client)
        want = months_back(last, months)
        sem = asyncio.Semaphore(4)

        async def one(m: str) -> list[dict[str, Any]]:
            async with sem:
                return await crimes(client, lat, lon, m)

        got, team = await asyncio.gather(asyncio.gather(*(one(m) for m in want)), neighbourhood(client, lat, lon))
    finally:
        if own:
            await client.aclose()
    every = [r for month in got for r in month]
    out = {"lat": round(lat, 5), "lon": round(lon, 5), "radius_miles": 1, "from": want[0], "month": last, "months": months,
           **summarise(every, names), "per_month": round(len(every) / months, 1), "latest": len(got[-1]),
           "trend": [{"month": m, "total": len(r)} for m, r in zip(want, got, strict=True)],
           "neighbourhood": team, "attribution": ATTRIBUTION}  # fmt: skip
    if not team and not every:
        out["note"] = "No police force covers this point (data.police.uk covers England, Wales and Northern Ireland)."
    return out


def words(d: dict[str, Any]) -> str:
    """The summary in a sentence or three."""
    month = datetime.strptime(d["month"], "%Y-%m").strftime("%B %Y")
    cats = d.get("by_category") or []
    if d.get("months", 1) > 1:
        s = (f"In the {d['months']} months to {month}, {d['total']} crimes were reported within a mile "
             f"(about {d['per_month']:g} a month; {d['latest']} in {month.split()[0]})")
    else:
        s = f"{d['total']} crimes reported within a mile in {month}"
    if cats:
        s += ": most " + ", ".join(f"{c['name'].lower()} ({c['count']})" for c in cats[:3])
    s += "."
    done = (d.get("outcomes") or {}).get("someone dealt with")
    if d["total"] and done is not None:
        s += f" Someone was dealt with for {round(100 * done / d['total'])}%."
    t = d.get("neighbourhood")
    if t:
        phone = (t.get("contact") or {}).get("telephone")
        s += f" Police team: {t['name']} ({t['force_name']})" + (f", {phone}" if phone else "") + "."
        if t.get("priorities"):
            s += f" Its latest priority: {t['priorities'][0]['issue'][:160]}."
    if d.get("note"):
        s = d["note"]
    return s


async def point_for(request: Request, postcode: str | None) -> tuple[float, float]:
    """A postcode's point (the local table first, then postcodes.io), for routes that take lat and lon or a postcode."""
    if not (postcode or "").strip():
        raise HTTPException(422, "Give lat and lon, or a postcode")
    from app.geo import PostcodeError, lookup_postcode
    from app.postcodes import local_postcode

    got = await local_postcode(request.app.state.engine, postcode.strip())
    if got is None:
        try:
            got = await lookup_postcode(postcode.strip())
        except PostcodeError as exc:
            raise HTTPException(404, f"Could not find the postcode {postcode.strip().upper()!r} ({exc})") from exc
    return got.lat, got.lon


@router.get("")
async def crime(request: Request, lat: float | None = Query(None, ge=49.5, le=61.0), lon: float | None = Query(None, ge=-8.7, le=2.0),
                postcode: str | None = Query(None, max_length=10),
                months: int = Query(12, ge=1, le=12, description="how many months up to the latest published")) -> dict[str, Any]:  # fmt: skip
    """Street-level crime within a mile and the local police team, for a point or a postcode."""
    if lat is None or lon is None:
        lat, lon = await point_for(request, postcode)
    try:
        d = await look_up(lat, lon, months)
    except CrimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {**d, "text": words(d)}


if __name__ == "__main__":  # the live check's probe: python -m app.crime LAT LON
    import json
    import sys

    a, b = float(sys.argv[1]), float(sys.argv[2])
    try:
        out = asyncio.run(look_up(a, b, 2))
    except CrimeError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
    print(words(out))
    # not where it was asked for (home): the counts, kinds, months and police team only
    print(json.dumps({k: v for k, v in out.items() if k not in ("attribution", "lat", "lon", "streets")}, indent=1, default=str)[:3000])

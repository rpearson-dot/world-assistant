"""Safety recalls for your bikes and parts: a weekly look at the UK government's product safety alerts, reports and
recalls (the Office for Product Safety and Standards, published on GOV.UK) for the makers of your bikes and parts.

GOV.UK's search API is public and needs no key. A notice matches when it names one of your makers and is about bikes
(cycling words in it); a match is a prompt to read the notice, not a verdict that your part is affected.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

log = logging.getLogger(__name__)
SEARCH = "https://www.gov.uk/api/search.json"
FORMAT = "product_safety_alert_report_recall"
UA = "WorldAssistant/1.0 (home bike garage; weekly recall check)"
BIKE_WORDS = re.compile(r"\b(bike|bikes|bicycle|bicycles|cycle|cycles|cycling|e-?bike|ebike|helmet|derailleur|crank|"
                        r"brake|pedal|saddle|handlebar|fork|wheel|tyre|tire|chain|groupset|frame)\b", re.IGNORECASE)  # fmt: skip
YEARS = 3  # notices this recent


class RecallError(Exception):
    pass


def makers(bikes: list[dict[str, Any]], parts: list[dict[str, Any]]) -> list[str]:
    """The makers to look for: each bike's (from its picture's model, "Planet X Reiver" gives Planet X) and each fitted,
    kit or spare part's brand."""
    from app.bikes.art import MODELS

    out: set[str] = set()
    for b in bikes:
        if b.get("retired_at"):
            continue
        m = MODELS.get((b.get("config") or {}).get("model") or "")
        if m and m.get("maker"):
            out.add(m["maker"])
        rec = b.get("records") or {}
        if rec.get("maker"):
            out.add(str(rec["maker"]))
    for p in parts:
        if p.get("status") != "retired" and p.get("brand"):
            out.add(str(p["brand"]).strip())
    return sorted(x for x in out if len(x) >= 2)


async def search(client: httpx.AsyncClient, maker: str) -> list[dict[str, Any]]:
    r = await client.get(SEARCH, params={"filter_format": FORMAT, "q": maker, "count": 20, "order": "-public_timestamp",
                                         "fields": "title,link,public_timestamp,description"},
                         headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)  # fmt: skip
    if r.status_code >= 400:
        raise RecallError(f"GOV.UK search answered HTTP {r.status_code}")
    return list((r.json() or {}).get("results") or [])


def matches(maker: str, results: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    word = re.compile(rf"(?<![\w]){re.escape(maker)}(?![\w])", re.IGNORECASE)
    out = []
    for x in results:
        text = f"{x.get('title') or ''} {x.get('description') or ''}"
        when = str(x.get("public_timestamp") or "")[:10]
        try:
            old = now - datetime.fromisoformat(when).replace(tzinfo=UTC) > timedelta(days=365 * YEARS)
        except ValueError:
            old = False
        if old or not word.search(text) or not BIKE_WORDS.search(text):
            continue
        link = str(x.get("link") or "")
        out.append({"maker": maker, "title": x.get("title"), "date": when or None, "summary": (x.get("description") or "")[:300],
                    "link": link if link.startswith("http") else f"https://www.gov.uk{link}"})  # fmt: skip
    return out


async def check(bikes: list[dict[str, Any]], parts: list[dict[str, Any]], client: httpx.AsyncClient | None = None,
                now: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """Every maker looked up; the notices that match, newest first."""
    now = now or datetime.now(UTC)
    own = client is None
    client = client or httpx.AsyncClient()
    found: dict[str, dict[str, Any]] = {}
    errors = []
    try:
        for m in makers(bikes, parts):
            try:
                for x in matches(m, await search(client, m), now):
                    found.setdefault(x["link"], x)
            except (RecallError, httpx.HTTPError, ValueError) as exc:
                errors.append(f"{m}: {exc}")
    finally:
        if own:
            await client.aclose()
    out = {"checked_at": now.isoformat(), "makers": makers(bikes, parts),
           "matches": sorted(found.values(), key=lambda x: x.get("date") or "", reverse=True)}  # fmt: skip
    if errors:
        out["error"] = "; ".join(errors[:3])
    return out


if __name__ == "__main__":  # python -m app.bikes.recalls MAKER...: what GOV.UK has for each (reads only)
    import sys

    async def _main(names: list[str]) -> None:
        now = datetime.now(UTC)
        async with httpx.AsyncClient() as c:
            for m in names or ["SRAM"]:
                try:
                    got = await search(c, m)
                except (RecallError, httpx.HTTPError, ValueError) as exc:
                    print(f"{m}: failed: {exc}")
                    continue
                hits = matches(m, got, now)
                print(f"{m}: {len(got)} notices found, {len(hits)} about bikes in the last {YEARS} years")
                for x in got[:3]:
                    print(f"  {str(x.get('public_timestamp'))[:10]} {x.get('title')}")

    import asyncio

    asyncio.run(_main(sys.argv[1:]))

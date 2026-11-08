"""The Food Standards Agency: food hygiene ratings near a place, and food alerts (allergy alerts and product recalls)
checked against your shopping (no key; Open Government Licence v3.0).

    GET  /v1/food/hygiene?lat=&lon=     (or postcode=) rated places within half a mile, nearest first; name= to search
    GET  /v1/food/alerts                the latest alerts, and those that look like something on your list or usual
    POST /v1/food/alerts/check          check now (the worker does each morning)

Hygiene ratings are looked up when asked. Alerts are read each morning (the FSA publishes a handful a week) and
matched against the KitchenOwl shopping list and your usual products: a match by name can be wrong (a recall is for
particular packs, sizes and dates), so it says "check this", with the FSA's notice linked.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.auth import require_token

log = logging.getLogger("wa.food")
router = APIRouter(prefix="/v1/food", tags=["food"], dependencies=[Depends(require_token)])
RATINGS = "https://api.ratings.food.gov.uk"
ALERTS = "https://data.food.gov.uk/food-alerts/id"
ATTRIBUTION = "Food Standards Agency data, licensed under the Open Government Licence v3.0"
KEY = "food_alerts"  # app_setting: the last check, the alerts seen and the matches
KEEP_DAYS = 60
KINDS = {"AA": "Allergy alert", "PRIN": "Product recall", "FAFA": "Food alert for action"}
_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
STOP = {"and", "the", "with", "for", "pack", "packs", "of", "in", "a", "x", "g", "kg", "ml", "l", "free", "range", "organic",
        "fresh", "large", "small", "medium", "mini", "family", "size", "multipack", "each", "value"}  # fmt: skip


class FoodError(Exception):
    pass


# --- hygiene ratings -------------------------------------------------------------------------------------------------
def rating_text(v: Any) -> str:
    v = str(v or "")
    return {"AwaitingInspection": "awaiting inspection", "AwaitingPublication": "awaiting publication", "Exempt": "exempt",
            "Pass": "pass", "PassandEatSafe": "pass and Eat Safe", "ImprovementRequired": "improvement required"}.get(v, v)  # fmt: skip


def place_of(e: dict[str, Any]) -> dict[str, Any]:
    geo = e.get("geocode") or {}
    addr = ", ".join(x for x in (e.get(f"AddressLine{i}") for i in range(1, 5)) if x)

    def num(x: Any) -> float | None:
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    rated = str(e.get("RatingDate") or "")[:10] or None
    return {"id": e.get("FHRSID"), "name": e.get("BusinessName"), "type": e.get("BusinessType"), "address": addr or None,
            "postcode": e.get("PostCode"), "rating": str(e.get("RatingValue") or "") or None,
            "rating_text": rating_text(e.get("RatingValue")), "rated_on": rated if rated and rated > "1901" else None,
            "miles": round(num(e.get("Distance")) or 0, 2) if e.get("Distance") is not None else None,
            "lat": num(geo.get("latitude")), "lon": num(geo.get("longitude")),
            "url": f"https://ratings.food.gov.uk/business/{e['FHRSID']}" if e.get("FHRSID") else None}  # fmt: skip


async def hygiene(lat: float, lon: float, miles: float = 0.5, name: str | None = None, limit: int = 10,
                  client: httpx.AsyncClient | None = None) -> dict[str, Any]:  # fmt: skip
    key = ("hyg", round(lat, 4), round(lon, 4), miles, (name or "").lower(), limit)
    got = _cache.get(key)
    if got and time.time() - got[0] < 6 * 3600:
        return got[1]  # type: ignore[no-any-return]
    params: dict[str, Any] = {"latitude": f"{lat:.6f}", "longitude": f"{lon:.6f}", "maxDistanceLimit": miles,
                              "sortOptionKey": "Distance", "pageSize": limit, "pageNumber": 1}  # fmt: skip
    if name:
        params["name"] = name
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        r = await client.get(f"{RATINGS}/Establishments", params=params, headers={"x-api-version": "2", "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise FoodError(f"the food hygiene ratings could not be reached ({type(exc).__name__})") from exc
    finally:
        if own:
            await client.aclose()
    if r.status_code >= 400:
        raise FoodError(f"the food hygiene ratings answered HTTP {r.status_code}")
    d = r.json()
    places = [place_of(e) for e in d.get("establishments") or []]
    total = ((d.get("meta") or {}).get("totalCount")) or len(places)
    out = {"places": places, "total": total, "miles": miles, "attribution": ATTRIBUTION}
    _cache[key] = (time.time(), out)
    return out


def hygiene_words(d: dict[str, Any]) -> str:
    p = d.get("places") or []
    if not p:
        return f"No food businesses rated within {d.get('miles', 0.5):g} miles."
    low = [x for x in p if x["rating"] in ("0", "1", "2")]
    s = "; ".join(f"{x['name']}: {x['rating_text'] or '?'}" for x in p[:4])
    tail = f" Rated 2 or lower: {', '.join(x['name'] for x in low[:3])}." if low else ""
    return f"Nearest rated: {s}.{tail}"


# --- alerts ----------------------------------------------------------------------------------------------------------
def _label(x: Any) -> str:
    if isinstance(x, dict):
        v = x.get("label") or x.get("prefLabel") or x.get("commonName") or x.get("@id") or ""
        if isinstance(v, list):
            v = v[0] if v else ""
        if isinstance(v, dict):
            v = v.get("@value", "")
        return str(v)
    return str(x or "")


def _list(x: Any) -> list[Any]:
    return x if isinstance(x, list) else [x] if x else []


def alert_of(a: dict[str, Any]) -> dict[str, Any]:
    kinds = [str(t if not isinstance(t, dict) else t.get("@id", "")).rsplit("/", 1)[-1] for t in _list(a.get("type"))]
    kind = next((KINDS[k] for k in kinds if k in KINDS), None)
    if kind is None:
        notation = str(a.get("notation") or "")
        kind = next((v for k, v in KINDS.items() if f"-{k}-" in notation), "Alert")
    products = []
    for pd in _list(a.get("productDetails")):
        if isinstance(pd, dict):
            bits = [pd.get("productName"), pd.get("packSizeDescription")]
            products.append({"name": str(pd.get("productName") or "").strip(), "pack": pd.get("packSizeDescription"),
                             "text": " ".join(str(b) for b in bits if b).strip()})  # fmt: skip
    allergens, risk = [], []
    for pr in _list(a.get("problem")):
        if isinstance(pr, dict):
            allergens += [_label(x) for x in _list(pr.get("allergen")) if _label(x)]
            if pr.get("riskStatement"):
                risk.append(str(pr["riskStatement"]))
    return {"id": str(a.get("notation") or a.get("@id") or ""), "kind": kind, "title": str(a.get("title") or a.get("shortTitle") or ""),
            "created": str(a.get("created") or "")[:10] or None, "url": a.get("alertURL"),
            "business": _label(a.get("reportingBusiness")) or None, "products": products,
            "allergens": sorted(set(allergens)), "risk": " ".join(risk) or None}  # fmt: skip


async def fetch_alerts(since: datetime, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        r = await client.get(ALERTS, params={"_limit": 100, "_sort": "-created", "_view": "full",
                                             "since": since.strftime("%Y-%m-%dT%H:%M:%SZ")},
                             headers={"Accept": "application/json"})  # fmt: skip
    except httpx.HTTPError as exc:
        raise FoodError(f"the FSA's food alerts could not be reached ({type(exc).__name__})") from exc
    finally:
        if own:
            await client.aclose()
    if r.status_code >= 400:
        raise FoodError(f"the FSA's food alerts answered HTTP {r.status_code}")
    d = r.json()
    items = d.get("items") if isinstance(d, dict) else d
    return [alert_of(a) for a in items or [] if isinstance(a, dict)]


def tokens(s: str) -> set[str]:
    words = re.findall(r"[a-z]+", s.lower().replace("'s", ""))
    return {w.rstrip("s") if len(w) > 4 else w for w in words if w not in STOP and len(w) > 2}


def matches(alert: dict[str, Any], items: list[str], usual: list[str]) -> list[dict[str, Any]]:
    """What on the list or among the usual products looks like a product in the alert. A list item ("houmous")
    matches when all its words are in a product's name; a usual product ("Sainsbury's Houmous 200g") when most of the
    words of one of them are in the other's."""
    names = [p["text"] or p["name"] for p in alert["products"]] or [alert["title"]]
    whole = [tokens(n) | tokens(alert.get("business") or "") for n in names]
    found = []
    for it in items:
        t = tokens(it)
        if t and any(t <= w for w in whole):
            found.append({"what": it, "from": "list", "how": "possible"})
    for u in usual:
        t = tokens(u)
        for w in whole:
            common = t & w
            if t and w and len(common) >= 2 and len(common) >= 0.6 * min(len(t), len(w)):
                found.append({"what": u, "from": "usual", "how": "likely"})
                break
    return found


async def _get_state(engine: Any) -> dict[str, Any]:
    async with engine.connect() as c:
        v = (await c.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).scalar()
    return json.loads(v) if isinstance(v, str) else (v or {})


async def _put_state(engine: Any, st: dict[str, Any]) -> None:
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                        {"k": KEY, "v": json.dumps(st, default=str)})  # fmt: skip


async def _shopping(request: Any) -> tuple[list[str], list[str], str | None]:
    """The names on the shopping list and the usual products, or why they could not be read."""
    items: list[str] = []
    usual: list[str] = []
    why = None
    try:
        from app.shopping import current_items, usual_matches

        try:
            items = [i["name"] for i in await current_items(request)]
        except HTTPException as exc:
            why = str(exc.detail)
        except Exception as exc:  # noqa: BLE001 - KitchenOwl down: the alerts still come in
            why = f"the shopping list could not be read ({type(exc).__name__})"
        usual = sorted({str(p.get("name")) for p in (await usual_matches(request)).values() if isinstance(p, dict) and p.get("name")})
    except Exception as exc:  # noqa: BLE001 - shopping not set up
        why = why or f"the shopping list could not be read ({type(exc).__name__})"
    return items, usual, why


async def check(request: Any, now: datetime | None = None, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Read the alerts since the last check (first time: the last fortnight), match them against the shopping, keep
    the last KEEP_DAYS days."""
    engine = request.app.state.engine
    now = now or datetime.now(UTC)
    st = await _get_state(engine)
    since = now - timedelta(days=14)
    if st.get("checked_at"):
        since = min(datetime.fromisoformat(st["checked_at"]) - timedelta(days=2), now - timedelta(days=3))
    fresh = await fetch_alerts(since, client)
    items, usual, why = await _shopping(request)
    keep = {a["id"]: a for a in st.get("alerts") or [] if (a.get("created") or "") >= (now - timedelta(days=KEEP_DAYS)).date().isoformat()}
    new = [a for a in fresh if a["id"] and a["id"] not in keep]
    for a in fresh:
        if a["id"]:
            keep[a["id"]] = a
    for a in keep.values():
        a["matches"] = matches(a, items, usual)
    alerts = sorted(keep.values(), key=lambda a: a.get("created") or "", reverse=True)
    st = {"checked_at": now.isoformat(), "alerts": alerts, "shopping_note": why, "list_items": len(items), "usual": len(usual)}
    await _put_state(engine, st)
    return {"new": len(new), "matched": [a for a in alerts if a["matches"]], "checked": len(fresh), "shopping_note": why}


def alerts_words(st: dict[str, Any]) -> str:
    m = [a for a in st.get("alerts") or [] if a.get("matches")]
    if m:
        return "Check these: " + "; ".join(f"{a['title']} (looks like {a['matches'][0]['what']})" for a in m[:3]) + "."
    n = len(st.get("alerts") or [])
    return f"No food alerts look like anything on your list or among your usual products ({n} alerts in the last {KEEP_DAYS} days)."


async def food_alerts_check(ctx: dict[str, Any]) -> Any:
    from app.jobs import tracked
    from app.wishlist import _Req

    async def run() -> Any:
        return await check(_Req(ctx["engine"]))

    return await tracked(ctx["engine"], "food_alerts_check", run)


# --- routes ----------------------------------------------------------------------------------------------------------
@router.get("/hygiene")
async def get_hygiene(request: Request, lat: float | None = Query(None, ge=49.5, le=61.0), lon: float | None = Query(None, ge=-8.7, le=2.0),
                      postcode: str | None = Query(None, max_length=10), miles: float = Query(0.5, gt=0, le=5),
                      name: str | None = Query(None, max_length=80), limit: int = Query(10, ge=1, le=50)) -> dict[str, Any]:  # fmt: skip
    """Food hygiene ratings near a point, nearest first (search by name too)."""
    if lat is None or lon is None:
        from app.crime import point_for

        lat, lon = await point_for(request, postcode)
    try:
        d = await hygiene(lat, lon, miles, name, limit)
    except FoodError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {**d, "text": hygiene_words(d)}


@router.get("/alerts")
async def get_alerts(request: Request, limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    st = await _get_state(request.app.state.engine)
    if not st.get("checked_at"):  # never checked yet (the worker's first is the next morning): check now
        try:
            await check(request)
            st = await _get_state(request.app.state.engine)
        except FoodError as exc:
            st = {"shopping_note": f"not checked yet: {exc}"}
    alerts = st.get("alerts") or []
    return {"checked_at": st.get("checked_at"), "alerts": alerts[:limit], "matched": [a for a in alerts if a.get("matches")],
            "shopping_note": st.get("shopping_note"), "text": alerts_words(st), "attribution": ATTRIBUTION}  # fmt: skip


@router.post("/alerts/check")
async def check_now(request: Request) -> dict[str, Any]:
    try:
        return await check(request)
    except FoodError as exc:
        raise HTTPException(502, str(exc)) from exc


if __name__ == "__main__":  # the live check's probe: python -m app.food LAT LON
    import asyncio
    import sys

    async def probe(lat: float, lon: float) -> int:
        try:
            h = await hygiene(lat, lon, 0.5, None, 5)
            print("Hygiene: " + hygiene_words(h))
            a = await fetch_alerts(datetime.now(UTC) - timedelta(days=30))
            print(f"Food alerts in the last 30 days: {len(a)}")
            for x in a[:5]:
                print(f"  {x['created']} {x['kind']}: {x['title']} (products: {', '.join(p['name'] for p in x['products'])[:120]})")
        except FoodError as exc:
            print(f"FAILED: {exc}")
            return 1
        return 0

    sys.exit(asyncio.run(probe(float(sys.argv[1]), float(sys.argv[2]))))

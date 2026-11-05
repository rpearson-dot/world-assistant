"""Shopping: the family's KitchenOwl list, priced at Sainsbury's and Ocado.

KitchenOwl (docker-compose.kitchenowl.yml) holds the list; everyone edits it in the KitchenOwl app. World Assistant
only reads it, with a long-lived token (key ``kitchenowl_token``). The supermarket service
(docker-compose.supermarket.yml, services/supermarket) searches the retailers; World Assistant picks a product per
item and retailer, lets you confirm or change it, and remembers the choice per KitchenOwl item, so "Milk" means the
same pint every week. Searches are kept for six hours; delivery slots for an hour. Send to basket puts the chosen
products in a retailer's basket when asked (topped up, never doubled). Nothing is ever booked or bought from here.

Routes (all /v1/shopping):
  GET  /status         what is set up and reachable, the chosen list, each retailer's login
  GET  /lists          KitchenOwl's households and their lists, to choose from
  PUT  /settings       {household_id, list_id, retailers}
  GET  /list           the chosen list's items
  POST /list           {name, description}: add an item to it
  POST /price          price the list now ({refresh: true} ignores the six-hour cache)
  GET  /price          the last result
  PUT  /match          {item_key, retailer, product}: "this is what I mean"
  DELETE /match        {item_key, retailer}: forget it, go back to the best match
  GET  /slots          ?retailer=: next delivery slots (read only)
  GET  /basket/{retailer}, POST /basket/{retailer}: the basket, and sending the last priced list to it
  POST /login/{retailer}/start, POST /login/sainsburys/code, POST /session/{retailer}, POST /logout/{retailer}
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings, read_secret

router = APIRouter(prefix="/v1/shopping", tags=["shopping"], dependencies=[Depends(require_token)])

RETAILERS = ("sainsburys", "ocado")
LABEL = {"sainsburys": "Sainsbury's", "ocado": "Ocado"}
SETTINGS_KEY = "shopping"
LAST_KEY = "shopping_last_price"
SEARCH_TTL = timedelta(hours=6)
SLOTS_TTL = timedelta(hours=1)
BATCH = 40  # items per call to the supermarket service

# --- quantities ---------------------------------------------------------------------------------------------------

_COUNT_WORDS = (r"packs?|tins?|cans?|bottles?|jars?|bags?|boxes?|loaves|loaf|tubs?|punnets?|cartons?|bunch(?:es)?"
                r"|each|of")  # fmt: skip
_COUNT = re.compile(rf"^\s*(?:x|×)?\s*(\d{{1,2}})\s*(?:x|×)?\s*(?:{_COUNT_WORDS})?\s*$", re.IGNORECASE)
_LEADING = re.compile(rf"^\s*(?:x|×)?\s*(\d{{1,2}})\s*(?:x|×|{_COUNT_WORDS})\b\s*(.*)$", re.IGNORECASE)


def quantity(description: str | None) -> tuple[int, str]:
    """How many to buy, and what is left of the description to search with.

    "2", "x2", "2x", "3 tins" -> a count. "2x semi-skimmed" -> 2 and "semi-skimmed". Anything else ("500g",
    "semi-skimmed 4 pints") is a description of the product, not a count: one, searched with the name.
    """
    d = (description or "").strip()
    if not d:
        return 1, ""
    m = _COUNT.match(d)
    if m:
        return max(1, min(99, int(m.group(1)))), ""
    m = _LEADING.match(d)
    if m and m.group(2).strip():
        return max(1, min(99, int(m.group(1)))), m.group(2).strip()
    return 1, d


def query_for(name: str, rest: str) -> str:
    return re.sub(r"\s+", " ", f"{name} {rest}".strip())[:120]


# --- choosing and totalling --------------------------------------------------------------------------------------


def best(products: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The retailer's own top result that is in stock and has a price (their ranking puts the usual pick first)."""
    for p in products:
        if p.get("price") is not None and p.get("in_stock") is not False:
            return p
    return None


def choose(products: list[dict[str, Any]], usual: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    """The product for one item at one retailer, and why: usual (your confirmed one, found again), usual_missing
    (yours is not in the results, so the best match stands in), best, or none."""
    if usual:
        for p in products:
            if str(p.get("id")) == str(usual.get("id")) and p.get("price") is not None:
                return p, "usual"
        b = best(products)
        return b, "usual_missing" if b else "none"
    b = best(products)
    return b, "best" if b else "none"


def totals(items: list[dict[str, Any]], retailers: list[str]) -> dict[str, Any]:
    """Each retailer's total for what it has, how many it lacks, and a like-for-like total over the items every
    retailer has (the fair comparison). The cheaper retailer is the one with the lower like-for-like total."""
    out: dict[str, Any] = {}
    both = [i for i in items if all(i["retailers"].get(r, {}).get("choice") for r in retailers)]
    for r in retailers:
        priced = [i for i in items if i["retailers"].get(r, {}).get("choice")]
        out[r] = {
            "label": LABEL[r],
            "total": round(sum(i["retailers"][r]["line"] for i in priced), 2),
            "items": len(priced),
            "missing": len(items) - len(priced),
            "like_for_like": round(sum(i["retailers"][r]["line"] for i in both), 2),
        }
    cheaper = None
    if len(retailers) > 1 and both:
        ranked = sorted(retailers, key=lambda r: out[r]["like_for_like"])
        if out[ranked[0]]["like_for_like"] < out[ranked[1]]["like_for_like"]:
            cheaper = ranked[0]
    return {"by_retailer": out, "compared_items": len(both), "cheaper": cheaper,
            "saving": round(out[ranked[1]]["like_for_like"] - out[ranked[0]]["like_for_like"], 2) if cheaper else 0.0}  # fmt: skip


def price_items(
    items: list[dict[str, Any]], retailers: list[str], found: dict[tuple[str, str], dict[str, Any]],
    usual: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Each list item with, per retailer, the candidates, the chosen product, the line price and any error.
    found[(retailer, query)] = {"products": [...], "error": str | None}; usual[(item_key, retailer)] = product."""
    out = []
    for it in items:
        qty, rest = quantity(it.get("description"))
        q = query_for(it["name"], rest)
        row = {"key": it["key"], "name": it["name"], "description": it.get("description") or "", "quantity": qty,
               "query": q, "retailers": {}}  # fmt: skip
        for r in retailers:
            mine = usual.get((it["key"], r))
            res = found.get((r, q), {"products": [], "error": "not searched"})
            products = list(res.get("products") or [])
            if mine:  # the usual one may only turn up when searched by its own name
                extra = found.get((r, query_for(mine.get("name", ""), "")), {}).get("products") or []
                products += [p for p in extra if all(str(p.get("id")) != str(x.get("id")) for x in products)]
            choice, why = choose(products, mine)
            row["retailers"][r] = {
                "choice": choice, "why": why, "usual": mine, "candidates": products[:8], "error": res.get("error"),
                "line": round(choice["price"] * qty, 2) if choice else 0.0,
            }
        out.append(row)
    return out


# --- settings and storage -----------------------------------------------------------------------------------------


class SettingsIn(BaseModel):
    household_id: int | None = None
    list_id: int | None = None
    retailers: list[str] = Field(default_factory=lambda: list(RETAILERS))


async def _get(request: Request, key: str) -> Any:
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": key})).first()
    return row[0] if row else None


async def _put(request: Request, key: str, value: Any) -> None:
    async with request.app.state.engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
            {"k": key, "v": json.dumps(value)},
        )  # fmt: skip


async def load_settings(request: Request) -> dict[str, Any]:
    s = await _get(request, SETTINGS_KEY) or {}
    retailers = [r for r in s.get("retailers") or RETAILERS if r in RETAILERS]
    return {"household_id": s.get("household_id"), "list_id": s.get("list_id"), "retailers": retailers}


# --- KitchenOwl ---------------------------------------------------------------------------------------------------


def _kitchenowl() -> tuple[str, str]:
    s = get_settings()
    return s.kitchenowl_url.rstrip("/"), read_secret("kitchenowl_token")


async def kitchenowl_get(path: str, transport: httpx.AsyncBaseTransport | None = None) -> Any:
    base, token = _kitchenowl()
    if not base:
        raise HTTPException(409, "KitchenOwl is not set up: add docker-compose.kitchenowl.yml (docs/shopping.md)")
    if not token:
        raise HTTPException(409, "No KitchenOwl token yet: make a long-lived token in the KitchenOwl app and save it here")
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as c:
            r = await c.get(base + path, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"KitchenOwl did not answer ({type(exc).__name__}); is its container running?") from exc
    if r.status_code in (401, 403, 422):
        raise HTTPException(409, "KitchenOwl refused the token: make a new long-lived token and save it here")
    if r.status_code >= 400:
        raise HTTPException(502, f"KitchenOwl answered HTTP {r.status_code}")
    return r.json()


async def kitchenowl_post(path: str, body: Any, transport: httpx.AsyncBaseTransport | None = None) -> Any:
    base, token = _kitchenowl()
    if not base:
        raise HTTPException(409, "KitchenOwl is not set up: add docker-compose.kitchenowl.yml (docs/shopping.md)")
    if not token:
        raise HTTPException(409, "No KitchenOwl token yet: make a long-lived token in the KitchenOwl app and save it here")
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as c:
            r = await c.post(base + path, json=body, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"KitchenOwl did not answer ({type(exc).__name__}); is its container running?") from exc
    if r.status_code in (401, 403, 422):
        raise HTTPException(409, "KitchenOwl refused the token: make a new long-lived token and save it here")
    if r.status_code >= 400:
        raise HTTPException(502, f"KitchenOwl answered HTTP {r.status_code}")
    return r.json()


async def add_item(request: Request, name: str, description: str = "",
                   transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:  # fmt: skip
    """Put an item on the chosen KitchenOwl list, as the KitchenOwl app does (it reuses the household's item of that
    name, so your usual product for it still applies). Already on the list: left as it is."""
    name = re.sub(r"\s+", " ", name).strip()[:120]
    if not name:
        raise HTTPException(422, "Say what to add")
    st = await load_settings(request)
    if not st["household_id"] or not st["list_id"]:
        raise HTTPException(409, "Choose the KitchenOwl list first (Shopping page)")
    lists = await kitchenowl_get(f"/api/household/{st['household_id']}/shoppinglist", transport)
    have = next((i for i in list_items(lists, st["list_id"]) if i["name"].lower() == name.lower()), None)
    if have:
        return {"added": False, "already": True, "item": have}
    body: dict[str, Any] = {"name": name}
    if description.strip():
        body["description"] = description.strip()[:120]
    got = await kitchenowl_post(f"/api/shoppinglist/{st['list_id']}/add-item-by-name", body, transport)
    return {"added": True, "already": False, "item": {"key": f"ko:{got.get('id')}", "name": got.get("name", name),
                                                       "description": body.get("description", "")}}  # fmt: skip


def list_items(lists: list[dict[str, Any]], list_id: int) -> list[dict[str, Any]]:
    """The items of one list from KitchenOwl's /household/{id}/shoppinglist answer, keyed by KitchenOwl item id."""
    for sl in lists:
        if sl.get("id") == list_id:
            return [{"key": f"ko:{i['id']}", "name": str(i.get("name") or "").strip(), "description": i.get("description") or "",
                     "category": (i.get("category") or {}).get("name") if isinstance(i.get("category"), dict) else None}
                    for i in sl.get("items") or [] if str(i.get("name") or "").strip()]  # fmt: skip
    raise HTTPException(409, "The chosen KitchenOwl list is gone: choose the list again")


async def current_items(request: Request, transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    st = await load_settings(request)
    if not st["household_id"] or not st["list_id"]:
        raise HTTPException(409, "Choose the KitchenOwl list first")
    lists = await kitchenowl_get(f"/api/household/{st['household_id']}/shoppinglist", transport)
    return list_items(lists, st["list_id"])


# --- the supermarket service --------------------------------------------------------------------------------------


async def service(method: str, path: str, body: Any = None, timeout: float = 60,
                  transport: httpx.AsyncBaseTransport | None = None) -> Any:  # fmt: skip
    base = get_settings().supermarket_url.rstrip("/")
    if not base:
        raise HTTPException(409, "The supermarket service is not set up: add docker-compose.supermarket.yml (docs/shopping.md)")
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as c:
            r = await c.request(method, base + path, json=body,
                                headers={"Authorization": f"Bearer {read_secret('supermarket_api_token')}"})  # fmt: skip
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"The supermarket service did not answer ({type(exc).__name__})") from exc
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        msg = data.get("error") if isinstance(data, dict) else None
        raise HTTPException(r.status_code if r.status_code < 500 else 502, msg or f"the supermarket service answered HTTP {r.status_code}")
    return data


async def service_image(path: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[bytes, str]:
    """A picture from the service (a login screenshot) and the name it was kept under."""
    base = get_settings().supermarket_url.rstrip("/")
    if not base:
        raise HTTPException(409, "The supermarket service is not set up: add docker-compose.supermarket.yml (docs/shopping.md)")
    try:
        async with httpx.AsyncClient(timeout=20, transport=transport) as c:
            r = await c.get(base + path, headers={"Authorization": f"Bearer {read_secret('supermarket_api_token')}"})
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"The supermarket service did not answer ({type(exc).__name__})") from exc
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/png"):
        raise HTTPException(404 if r.status_code in (200, 404) else 502, "No screenshot kept for that retailer")
    return r.content, r.headers.get("x-screenshot-name", "")


async def search_all(request: Request, wanted: dict[str, set[str]], refresh: bool) -> dict[tuple[str, str], dict[str, Any]]:
    """Every (retailer, query) result, from the six-hour store where it is fresh, else from the service."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    since = datetime.now(UTC) - SEARCH_TTL
    async with request.app.state.engine.connect() as conn:
        for r, queries in wanted.items():
            if refresh or not queries:
                continue
            rows = await conn.execute(
                text("SELECT query, products, error FROM shopping_search WHERE retailer = :r AND query = ANY(:q) "
                     "AND fetched_at > :since AND error IS NULL"),
                {"r": r, "q": sorted(queries), "since": since},
            )  # fmt: skip
            for q, products, error in rows:
                found[(r, q)] = {"products": products, "error": error}
    todo: dict[str, list[str]] = {r: sorted(q for q in qs if (r, q) not in found) for r, qs in wanted.items()}
    for r, queries in todo.items():
        for i in range(0, len(queries), BATCH):
            chunk = queries[i : i + BATCH]
            data = await service("POST", "/compare", {"items": [{"key": q, "query": q} for q in chunk], "providers": [r],
                                                      "limit": 6}, timeout=300)  # fmt: skip
            async with request.app.state.engine.begin() as conn:
                for res in data.get("results", []):
                    got = (res.get("providers") or {}).get(r) or {"products": [], "error": "no answer"}
                    found[(r, res["query"])] = got
                    await conn.execute(
                        text("INSERT INTO shopping_search (retailer, query, products, error, fetched_at) "
                             "VALUES (:r, :q, CAST(:p AS jsonb), :e, now()) ON CONFLICT (retailer, query) DO UPDATE "
                             "SET products = EXCLUDED.products, error = EXCLUDED.error, fetched_at = now()"),
                        {"r": r, "q": res["query"], "p": json.dumps(got.get("products") or []), "e": got.get("error")},
                    )  # fmt: skip
    return found


async def usual_matches(request: Request) -> dict[tuple[str, str], dict[str, Any]]:
    async with request.app.state.engine.connect() as conn:
        rows = await conn.execute(text("SELECT item_key, retailer, product FROM shopping_match"))
        return {(k, r): p for k, r, p in rows}


# --- routes -------------------------------------------------------------------------------------------------------


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    """What is set up and reachable. Always answers; each part says what is wrong with it."""
    s = get_settings()
    st = await load_settings(request)
    out: dict[str, Any] = {"settings": st, "kitchenowl": {"installed": bool(s.kitchenowl_url), "public_url": s.kitchenowl_public_url,
                           "token": bool(read_secret("kitchenowl_token"))}}  # fmt: skip
    try:
        hh = await kitchenowl_get("/api/household")
        out["kitchenowl"].update(ok=True, households=len(hh))
    except HTTPException as exc:
        out["kitchenowl"].update(ok=False, problem=exc.detail)
    out["supermarket"] = {"installed": bool(s.supermarket_url)}
    try:
        h = await service("GET", "/health", timeout=10)
        out["supermarket"].update(ok=True, providers=h.get("providers"), login=h.get("login"))
    except HTTPException as exc:
        out["supermarket"].update(ok=False, problem=exc.detail)
    out["credentials"] = {r: bool(read_secret(f"{r}_email") and read_secret(f"{r}_password")) for r in RETAILERS}
    return out


@router.get("/lists")
async def lists() -> list[dict[str, Any]]:
    """Each household with its shopping lists (id, name, how many items), to choose from."""
    households = await kitchenowl_get("/api/household")
    out = []
    for h in households:
        sl = await kitchenowl_get(f"/api/household/{h['id']}/shoppinglist")
        out.append({"id": h["id"], "name": h.get("name", ""),
                    "lists": [{"id": x["id"], "name": x.get("name", ""), "items": len(x.get("items") or [])} for x in sl]})  # fmt: skip
    return out


@router.put("/settings")
async def save_settings(body: SettingsIn, request: Request) -> dict[str, Any]:
    bad = [r for r in body.retailers if r not in RETAILERS]
    if bad or not body.retailers:
        raise HTTPException(422, f"retailers: choose from {', '.join(RETAILERS)}")
    value = {"household_id": body.household_id, "list_id": body.list_id, "retailers": list(dict.fromkeys(body.retailers))}
    await _put(request, SETTINGS_KEY, value)
    return value


@router.get("/list")
async def get_list(request: Request) -> dict[str, Any]:
    items = await current_items(request)
    return {"items": items, "count": len(items)}


class PriceIn(BaseModel):
    refresh: bool = False


class AddIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=120)


@router.post("/list")
async def add_to_list(body: AddIn, request: Request) -> dict[str, Any]:
    """Add an item to the KitchenOwl list (as the assistant's add_to_list does)."""
    return await add_item(request, body.name, body.description)


@router.post("/price")
async def price(body: PriceIn, request: Request) -> dict[str, Any]:
    """Price the list at each chosen retailer: best match or your usual per item, totals, and the cheaper one."""
    st = await load_settings(request)
    items = await current_items(request)
    if not items:
        raise HTTPException(409, "The list is empty")
    usual = await usual_matches(request)
    wanted: dict[str, set[str]] = {r: set() for r in st["retailers"]}
    for it in items:
        q = query_for(it["name"], quantity(it["description"])[1])
        for r in st["retailers"]:
            wanted[r].add(q)
            mine = usual.get((it["key"], r))
            if mine and mine.get("name"):
                wanted[r].add(query_for(mine["name"], ""))
    found = await search_all(request, wanted, body.refresh)
    rows = price_items(items, st["retailers"], found, usual)
    result = {"priced_at": datetime.now(UTC).isoformat(), "retailers": st["retailers"], "items": rows,
              "totals": totals(rows, st["retailers"])}  # fmt: skip
    await _put(request, LAST_KEY, result)
    return result


@router.get("/price")
async def last_price(request: Request) -> dict[str, Any]:
    last = await _get(request, LAST_KEY)
    if not last:
        raise HTTPException(404, "The list has not been priced yet")
    return last  # type: ignore[no-any-return]


class Product(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=300)
    price: float | None = None
    size: str | None = None
    unit_price: dict[str, Any] | None = None


class MatchIn(BaseModel):
    item_key: str = Field(min_length=1, max_length=80)
    retailer: str
    product: Product | None = None  # none: forget the usual one


def _retailer(r: str) -> str:
    if r not in RETAILERS:
        raise HTTPException(422, f"retailer: one of {', '.join(RETAILERS)}")
    return r


@router.put("/match")
async def set_match(body: MatchIn, request: Request) -> dict[str, Any]:
    _retailer(body.retailer)
    if not body.product:
        raise HTTPException(422, "product: which product you mean")
    async with request.app.state.engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO shopping_match (item_key, retailer, product, confirmed_at) VALUES (:k, :r, CAST(:p AS jsonb), now()) "
                 "ON CONFLICT (item_key, retailer) DO UPDATE SET product = EXCLUDED.product, confirmed_at = now()"),
            {"k": body.item_key, "r": body.retailer, "p": body.product.model_dump_json()},
        )  # fmt: skip
    return {"item_key": body.item_key, "retailer": body.retailer, "product": body.product.model_dump()}


@router.delete("/match")
async def forget_match(body: MatchIn, request: Request) -> dict[str, Any]:
    _retailer(body.retailer)
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text("DELETE FROM shopping_match WHERE item_key = :k AND retailer = :r"),
                           {"k": body.item_key, "r": body.retailer})  # fmt: skip
    return {"item_key": body.item_key, "retailer": body.retailer, "product": None}


_slots_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}


@router.get("/slots")
async def slots(retailer: str = "", refresh: bool = False) -> dict[str, Any]:
    """The next delivery slots at one retailer, read only, kept for an hour (Sainsbury's needs a browser: 10-20 s)."""
    if retailer not in RETAILERS:
        raise HTTPException(422, f"retailer: one of {', '.join(RETAILERS)}")
    hit = _slots_cache.get(retailer)
    if hit and not refresh and datetime.now(UTC) - hit[0] < SLOTS_TTL:
        return {**hit[1], "cached": True, "fetched_at": hit[0].isoformat()}
    data = await service("GET", f"/slots?provider={retailer}", timeout=180)
    now = datetime.now(UTC)
    _slots_cache[retailer] = (now, data)
    return {**data, "cached": False, "fetched_at": now.isoformat()}


def basket_items(last: dict[str, Any] | None, retailer: str) -> list[dict[str, Any]]:
    """What the last pricing chose at one retailer, as basket lines: the product id, its name and how many."""
    if not last or retailer not in (last.get("retailers") or []):
        return []
    out = []
    for it in last.get("items") or []:
        x = (it.get("retailers") or {}).get(retailer) or {}
        c = x.get("choice")
        if c and c.get("id"):
            out.append({"id": str(c["id"]), "name": c.get("name") or it.get("name", ""), "quantity": int(it.get("quantity") or 1),
                        "item": it.get("name", "")})  # fmt: skip
    return out


def _login_first(retailer: str, exc: HTTPException) -> HTTPException:
    """The retailer turning the basket away because nobody is logged in reads as that, not as an HTTP code."""
    words = str(exc.detail)
    if any(w in words for w in ("401", "403", "logged out", "session expired", "No CSRF token")):
        return HTTPException(409, f"Log in to {LABEL[retailer]} first (Retailer logins, below), then send again")
    return exc


@router.get("/basket/{retailer}")
async def get_basket(retailer: str) -> dict[str, Any]:
    """What is in the retailer's basket now (needs that retailer's login)."""
    _retailer(retailer)
    try:
        return await service("GET", f"/basket?provider={retailer}", timeout=90)  # type: ignore[no-any-return]
    except HTTPException as exc:
        raise _login_first(retailer, exc) from exc


@router.post("/basket/{retailer}")
async def send_to_basket(retailer: str, request: Request) -> dict[str, Any]:
    """Put the last priced list's products for this retailer in its basket, each up to the quantity on the list. Items
    already there are topped up, never doubled. It never books a slot or checks out: you finish on their site."""
    _retailer(retailer)
    items = basket_items(await _get(request, LAST_KEY), retailer)
    if not items:
        raise HTTPException(409, f"Price the list first: there is nothing chosen at {LABEL[retailer]} to send")
    try:
        out = await service("POST", "/basket/add", {"provider": retailer, "items": items}, timeout=600)
    except HTTPException as exc:
        raise _login_first(retailer, exc) from exc
    names = {i["id"]: i["item"] for i in items}
    for r in out.get("results", []):
        r["item"] = names.get(r.get("id"), "")
    failed = [r for r in out.get("results", []) if r.get("status") == "failed"]
    if failed and len(failed) == len(items) and any("401" in str(r.get("error")) or "403" in str(r.get("error")) for r in failed):
        raise HTTPException(409, f"Log in to {LABEL[retailer]} first (Retailer logins, below), then send again")
    return out  # type: ignore[no-any-return]


class CodeIn(BaseModel):
    code: str = Field(pattern=r"^\s*[A-Za-z0-9]{4,10}\s*$")


class CookiesIn(BaseModel):
    cookies: Any


@router.post("/login/{retailer}/start")
async def login_start(retailer: str) -> dict[str, Any]:
    """Log in with the email and password saved on the Shopping page. Sainsbury's then waits for its SMS code."""
    _retailer(retailer)
    email, password = read_secret(f"{retailer}_email"), read_secret(f"{retailer}_password")
    if not email or not password:
        raise HTTPException(409, f"Save the {LABEL[retailer]} email and password first")
    return await service("POST", "/login/start", {"provider": retailer, "email": email, "password": password}, timeout=120)  # type: ignore[no-any-return]


@router.post("/login/{retailer}/code")
async def login_code(retailer: str, body: CodeIn) -> dict[str, Any]:
    """The code the retailer texted (Sainsbury's) or emailed, while its login waits."""
    _retailer(retailer)
    _slots_cache.pop(retailer, None)
    return await service("POST", "/login/code", {"code": body.code.strip(), "provider": retailer}, timeout=60)  # type: ignore[no-any-return]


@router.post("/login/{retailer}/continue")
async def login_continue(retailer: str) -> dict[str, Any]:
    """After clicking the link the retailer emailed: look again, and finish the login if it is confirmed."""
    _retailer(retailer)
    _slots_cache.pop(retailer, None)
    return await service("POST", "/login/continue", {"provider": retailer}, timeout=90)  # type: ignore[no-any-return]


@router.get("/login/{retailer}/screenshot", response_class=Response)
async def login_screenshot(retailer: str) -> Response:
    """The newest screenshot of the retailer's sign-in page: what it showed when a login failed or waited."""
    _retailer(retailer)
    data, name = await service_image(f"/login/screenshot?provider={retailer}")
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store", "X-Screenshot-Name": name})


@router.post("/session/{retailer}")
async def import_session(retailer: str, body: CookiesIn) -> dict[str, Any]:
    """Cookies exported from your own browser after logging in there: the fallback when the login here is refused."""
    _retailer(retailer)
    _slots_cache.pop(retailer, None)
    return await service("POST", "/session/import", {"provider": retailer, "cookies": body.cookies})  # type: ignore[no-any-return]


@router.post("/logout/{retailer}")
async def logout(retailer: str) -> dict[str, Any]:
    _retailer(retailer)
    _slots_cache.pop(retailer, None)
    return await service("POST", "/logout", {"provider": retailer})  # type: ignore[no-any-return]

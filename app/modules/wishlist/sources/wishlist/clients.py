"""Aldi's and Lidl's own product search, read for the wishlist.

Neither publishes an API: these are the JSON searches their websites call. They are unofficial, so they are read
gently (one search per wish per retailer, twice a week, or when asked) and every field is looked for under several
names, since either can change without notice. `python -m app.modules.wishlist.wishlist <words>` shows what each one sends.

    Aldi  https://api.aldi.co.uk/v3/product-search?q=...   in-store (walk-in) range, Specialbuys included
    Lidl  https://www.lidl.co.uk/q/api/search?q=...        the online and in-store range, Middle of Lidl included
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.version import VERSION

ALDI = "https://api.aldi.co.uk/v3/product-search"
LIDL = "https://www.lidl.co.uk/q/api/search"
SITES = {"aldi": "https://www.aldi.co.uk", "lidl": "https://www.lidl.co.uk"}
NAMES = {"aldi": "Aldi", "lidl": "Lidl"}
HEADERS = {"User-Agent": f"WorldAssistant/{VERSION} (personal wishlist, twice a week)",
           "Accept": "application/json", "Accept-Language": "en-GB,en;q=0.9"}  # fmt: skip
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
GONE = ("sold out", "no longer available", "out of stock", "unavailable", "ended")
# words that mean the same thing in a product's name, so "kamado bbq" finds a "Kamado Ceramic Charcoal Grill"
SAME = {"bbq": ("bbq", "barbecue", "barbeque", "grill"), "barbecue": ("bbq", "barbecue", "barbeque", "grill"),
        "tv": ("tv", "television"), "hoover": ("hoover", "vacuum"), "jumper": ("jumper", "sweater", "knit")}  # fmt: skip


def uk_today() -> date:
    return datetime.now(ZoneInfo("Europe/London")).date()


class ShopError(Exception):
    """A retailer could not be read: the message says why, in words."""


# --- reading whatever shape comes back -------------------------------------------------------------------------
def _at(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(obj, list):
            obj = next((x for x in obj if isinstance(x, dict)), None)
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _first(obj: dict[str, Any], *paths: str) -> tuple[str | None, Any]:
    for p in paths:
        v = _at(obj, p)
        if v not in (None, "", [], {}):
            return p, v
    return None, None


def _texts(obj: Any, depth: int = 0) -> list[str]:
    """Every short string inside (badges, ribbons, labels), to read availability and dates from."""
    if depth > 4:
        return []
    if isinstance(obj, str):
        return [obj] if 0 < len(obj) < 120 else []
    if isinstance(obj, dict):
        return [t for k, v in obj.items() if k.lower() in TEXT_KEYS or isinstance(v, (dict, list)) for t in _texts(v, depth + 1)]
    if isinstance(obj, list):
        return [t for v in obj[:12] for t in _texts(v, depth + 1)]
    return []


TEXT_KEYS = {"text", "label", "title", "name", "value", "availability", "availabilityindicator", "message", "info"}
TITLE_KEYS = ("fullTitle", "name", "title", "productName")


def products_in(obj: Any, depth: int = 0) -> list[dict[str, Any]]:
    """The dicts that look like products: a title and a price somewhere in them."""
    if depth > 8:
        return []
    if isinstance(obj, list):
        return [p for v in obj for p in products_in(v, depth + 1)]
    if not isinstance(obj, dict):
        return []
    if any(isinstance(obj.get(k), str) for k in TITLE_KEYS) and "price" in obj:
        return [obj]
    return [p for v in obj.values() if isinstance(v, (dict, list)) for p in products_in(v, depth + 1)]


def _money(path: str | None, v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return round(v / 100, 2) if path and path.endswith("amount") and isinstance(v, int) else round(float(v), 2)
    m = re.search(r"(\d+(?:[.,]\d{1,2})?)", str(v).replace(",", ""))
    if not m:
        return None
    n = float(m.group(1))
    return round(n / 100, 2) if "p" in str(v).lower() and "£" not in str(v) else round(n, 2)


def when(text: Any, today: date) -> date | None:
    """A date in what a retailer says ("Thu 12 Nov", "from 12/11", "2026-11-12"), in the year that puts it
    closest to today."""
    s = str(text or "").lower()
    if m := re.search(r"(20\d\d)-(\d\d)-(\d\d)", s):
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    day = month = None
    if m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", s):
        day, month = int(m.group(1)), MONTHS[m.group(2)]
    elif m := re.search(r"\b(\d{1,2})[/.](\d{1,2})\b", s):
        day, month = int(m.group(1)), int(m.group(2))
    if not day or not month:
        return None
    best: date | None = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((d - today).days) < abs((best - today).days):
            best = d
    return best


def product(retailer: str, raw: dict[str, Any], today: date) -> dict[str, Any] | None:
    """One product as read here: id, name, brand, price, link, on-sale date, availability and a status:
    coming (on sale later), on sale, sold out, or listed (no date or stock said)."""
    _, name = _first(raw, *TITLE_KEYS)
    if not isinstance(name, str) or not name.strip():
        return None
    _, pid = _first(raw, "sku", "productId", "id", "erpNumber", "code", "itemId")
    _, brand = _first(raw, "brandName", "brand.name", "brand")
    ppath, pval = _first(raw, "price.amountRelevantDisplay", "price.price", "price.amount", "price.value", "price")
    _, slug = _first(raw, "canonicalUrl", "canonicalPath", "url", "urlSlugText", "slug")
    url = None
    if isinstance(slug, str):
        if slug.startswith("http"):
            url = slug
        elif slug.startswith("/"):
            url = SITES[retailer] + slug
        elif retailer == "aldi":
            url = f"{SITES['aldi']}/product/{slug}-{pid}" if pid else f"{SITES['aldi']}/product/{slug}"
    texts = _texts({k: v for k, v in raw.items() if k not in TITLE_KEYS and k != "description"})
    _, d = _first(raw, "onSaleDate", "onSaleDateDisplay", "storeStartDate", "availableFrom", "startDate",
                  "stockAvailability.availabilityIndicator", "releaseDate")  # fmt: skip
    on = when(d, today) if d else None
    if on is None:
        on = next((x for x in (when(t, today) for t in texts) if x), None)
    say = "; ".join(dict.fromkeys(t.strip() for t in texts if re.search(r"store|sale|stock|sold|avail|from|online|special|only", t, re.IGNORECASE)))[:200]
    # not "notForSale": Aldi sets it on everything sold only in its shops (not online), which is most Specialbuys
    gone = any(g in say.lower() for g in GONE) or raw.get("soldOut") is True
    status = ("sold out" if gone else "coming" if on and on > today else "on sale" if on or "in store now" in say.lower()
              or "available" in say.lower() else "listed")  # fmt: skip
    return {"retailer": retailer, "id": str(pid or re.sub(r"\W+", "-", name.lower()).strip("-")), "name": name.strip(),
            "brand": brand if isinstance(brand, str) else None, "price": _money(ppath, pval), "url": url,
            "on_sale": on.isoformat() if on else None, "status": status, "availability": say or None}  # fmt: skip


# --- what counts as the thing you want -------------------------------------------------------------------------
def _words(s: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9]+", " ", s.lower()).split() if len(w) > 1]


# a product with one of these in its name is for the thing, not the thing, unless you asked for one
ACCESSORY = {"cover", "covers", "case", "bag", "accessory", "accessories", "replacement", "spare", "spares", "refill",
             "refills", "liner", "skin", "protector", "adapter", "adaptor", "mount", "strap", "parts"}  # fmt: skip


def matches(name: str, query: str, must: list[str] | None = None) -> bool:
    """Every word you asked for is in the product's name (or a word that means the same, e.g. bbq and grill), and it
    is not an accessory for it (a cover, a bag, spares) unless that is what you asked for. `must`, if given, replaces
    the query's words."""
    have = set(_words(name))
    text = " ".join(_words(name))
    want = [w for m in (must or []) for w in _words(m)] or _words(query)
    asked = set(_words(query)) | set(want)
    if (have & ACCESSORY) - asked:
        return False
    return bool(want) and all(any(s in have or (len(s) > 4 and s in text) for s in SAME.get(w, (w,))) for w in want)


# --- the two searches -------------------------------------------------------------------------------------------
async def _get(client: httpx.AsyncClient, retailer: str, url: str, params: dict[str, Any]) -> Any:
    try:
        r = await client.get(url, params=params, headers=HEADERS)
    except httpx.HTTPError as exc:
        raise ShopError(f"{NAMES[retailer]} could not be reached ({type(exc).__name__})") from exc
    if r.status_code in (401, 403, 429):
        raise ShopError(f"{NAMES[retailer]} refused the search (HTTP {r.status_code}): it may be blocking automated requests")
    if r.status_code >= 400:
        raise ShopError(f"{NAMES[retailer]} answered HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as exc:
        raise ShopError(f"{NAMES[retailer]} did not send JSON (it may have changed its site)") from exc


GENERIC = {w for v in SAME.values() for w in v} | {"set", "large", "small", "new", "the", "and", "with", "for"}


def search_words(query: str) -> str:
    """What to ask the shop for: the item's distinctive words. The shops' searches match any word, so "kamado bbq"
    asked as it is brings back 177 BBQ sauces and snacks, and the Kamado (if listed) is not in the first page. Whether a
    product is the thing is still decided on every word of the item (with its synonyms), by matches()."""
    words = [w for w in re.findall(r"[\w'-]+", query) if w.lower() not in GENERIC]
    return " ".join(words) or query


async def search(client: httpx.AsyncClient, retailer: str, query: str, today: date | None = None,
                 raw: bool = False) -> Any:  # fmt: skip
    """The retailer's products for a search, as read here (or exactly as sent, with raw)."""
    today = today or uk_today()
    query = search_words(query)
    if retailer == "aldi":
        data = await _get(client, retailer, ALDI, {"currency": "GBP", "serviceType": "walk-in", "q": query,
                                                   "limit": 30, "offset": 0, "sort": "relevance"})  # fmt: skip
    elif retailer == "lidl":
        data = await _get(client, retailer, LIDL, {"q": query, "assortment": "GB", "locale": "en_GB",
                                                   "version": "v2.0.0", "offset": 0, "fetchsize": 36})  # fmt: skip
    else:
        raise ShopError(f"no retailer {retailer!r}")
    if raw:
        return data
    found = [p for p in (product(retailer, x, today) for x in products_in(data)) if p]
    return list({p["id"]: p for p in found}.values())

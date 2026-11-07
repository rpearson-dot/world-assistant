"""GO Outdoors, Decathlon and Bikesy prices, read for the Outdoors list.

GO Outdoors runs on Shopify, which publishes every shop's products as JSON at standard addresses: the search
(`/search/suggest.json`) and each product (`/products/<handle>.js`, prices in pence). Decathlon does not allow its
search to be read automatically, so only a product page someone has pasted in is read, for its schema.org Product
data (the price and stock it shows search engines). Bikesy compares 30-odd UK bike shops; its search pages (/s/) are
closed to automated readers, so its tag pages (/tag/<words>/, which its robots.txt leaves open) are read instead, for
each deal's name, price, full price and Bikesy's "View Deal" link (the listing does not say which shop).
`python -m app.outdoors <words or link>` shows what each sends.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.integrations.wishlist.clients import ShopError
from app.version import VERSION

GO = "https://www.gooutdoors.co.uk"
BIKESY = "https://bikesy.co.uk"
NAMES = {"gooutdoors": "GO Outdoors", "decathlon": "Decathlon", "bikesy": "Bikesy"}
HEADERS = {"User-Agent": f"WorldAssistant/{VERSION} (personal price watch, once a day)",
           "Accept-Language": "en-GB,en;q=0.9"}  # fmt: skip
URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
LDJSON = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL)


def links(text: str) -> list[tuple[str, str]]:
    """(retailer, url) for each GO Outdoors or Decathlon product link in an item's description."""
    out = []
    for u in URL.findall(text or ""):
        u = u.rstrip(").,;")
        host = (urlparse(u).hostname or "").lower()
        if host.endswith("gooutdoors.co.uk") and "/products/" in u:
            out.append(("gooutdoors", u.split("?")[0].split("#")[0]))
        elif host.endswith("decathlon.co.uk"):
            out.append(("decathlon", u.split("#")[0]))
    return out


def _pounds(v: Any, pence: bool = False) -> float | None:
    if v in (None, "") or isinstance(v, bool):
        return None
    try:
        n = float(str(v).replace("£", "").replace(",", ""))
    except ValueError:
        return None
    return round(n / 100, 2) if pence else round(n, 2)


async def _get(client: httpx.AsyncClient, retailer: str, url: str, params: dict[str, Any] | None = None,
               json_: bool = True) -> Any:  # fmt: skip
    try:
        r = await client.get(url, params=params, headers={**HEADERS, "Accept": "application/json" if json_ else "text/html"},
                             follow_redirects=True)  # fmt: skip
    except httpx.HTTPError as exc:
        raise ShopError(f"{NAMES[retailer]} could not be reached ({type(exc).__name__})") from exc
    if r.status_code in (401, 403, 429):
        raise ShopError(f"{NAMES[retailer]} refused the request (HTTP {r.status_code}): it may be blocking automated requests")
    if r.status_code == 404:
        raise ShopError(f"{NAMES[retailer]} has no such page any more (HTTP 404): the link may be out of date")
    if r.status_code >= 400:
        raise ShopError(f"{NAMES[retailer]} answered HTTP {r.status_code}")
    if not json_:
        return r.text
    try:
        return r.json()
    except ValueError as exc:
        raise ShopError(f"{NAMES[retailer]} did not send JSON (it may have changed its site)") from exc


def _go_product(p: dict[str, Any], pence: bool) -> dict[str, Any]:
    handle = p.get("handle") or str(p.get("url") or "").split("/products/")[-1].split("?")[0]
    price = _pounds(p.get("price_min") if p.get("price_min") not in (None, "") else p.get("price"), pence)
    was = _pounds(p.get("compare_at_price_max") or p.get("compare_at_price") or p.get("compare_at_price_min"), pence)
    return {"retailer": "gooutdoors", "id": str(handle), "name": str(p.get("title") or handle).strip(),
            "brand": p.get("vendor") or None, "price": price, "was": was if was and price and was > price else None,
            "available": bool(p.get("available", True)), "url": f"{GO}/products/{handle}"}  # fmt: skip


async def go_search(client: httpx.AsyncClient, query: str, raw: bool = False) -> Any:
    """GO Outdoors' products for a search (prices in pounds)."""
    data = await _get(client, "gooutdoors", f"{GO}/search/suggest.json", {
        "q": query, "resources[type]": "product", "resources[limit]": 10,
        "resources[options][unavailable_products]": "last"})  # fmt: skip
    if raw:
        return data
    found = (((data or {}).get("resources") or {}).get("results") or {}).get("products") or []
    return [_go_product(p, pence=False) for p in found if isinstance(p, dict)]


async def go_product(client: httpx.AsyncClient, url: str, raw: bool = False) -> Any:
    """One GO Outdoors product from its link (Shopify's product JSON, prices in pence)."""
    handle = url.split("/products/")[-1].split("?")[0].split("/")[0]
    data = await _get(client, "gooutdoors", f"{GO}/products/{handle}.js")
    return data if raw else _go_product(data, pence=True)


def _ld_products(html: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in LDJSON.findall(html):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                t = d.get("@type")
                if t == "Product" or (isinstance(t, list) and "Product" in t):
                    out.append(d)
                stack.extend(v for k, v in d.items() if k == "@graph" or isinstance(v, list))
    return out


async def decathlon_product(client: httpx.AsyncClient, url: str, raw: bool = False) -> Any:
    """One Decathlon product from its page: the schema.org Product it publishes (name, price, stock)."""
    html = await _get(client, "decathlon", url, json_=False)
    found = _ld_products(html)
    if raw:
        return found or html[:2000]
    if not found:
        raise ShopError("Decathlon's page had no product data (it may have changed its site, or blocked the request)")
    p = found[0]
    offers = p.get("offers") or {}
    offers = offers[0] if isinstance(offers, list) and offers else offers
    price = _pounds(offers.get("price") or offers.get("lowPrice"))
    stock = str(offers.get("availability") or "")
    brand = p.get("brand")
    return {"retailer": "decathlon", "id": str(p.get("sku") or p.get("productID") or url.rstrip("/").split("/")[-1]),
            "name": str(p.get("name") or "").strip() or url, "brand": brand.get("name") if isinstance(brand, dict) else brand,
            "price": price, "was": None, "available": "OutOfStock" not in stock and "SoldOut" not in stock,
            "url": url}  # fmt: skip


def _tag(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "+", query.lower()).strip("+")


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", html)).replace("&amp;", "&").replace("&pound;", "£").strip()


BADGE = re.compile(r"^(\d+% off|save|view deal|more info|sale|new|rrp|was|from|ex[- ]?demo|£|[\d.,]+$)", re.IGNORECASE)


def bikesy_deals(html: str) -> list[dict[str, Any]]:
    """The deals on a Bikesy listing page: each is the stretch of the page around its View Deal link
    (outbound.php?pid=N), with the name from the image's alt text or the deal's longest piece of text, the sale price
    (the lowest pound figure, leaving out the "SAVE" one) and the full price (the highest, when it is higher)."""
    html = html.replace("&pound;", "£")
    marks = [(m.start(), m.group(1)) for m in re.finditer(r"outbound\.php\?pid=(\d+)", html)]
    order: list[str] = []
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    for at, pid in marks:
        if pid not in first:
            order.append(pid)
            first[pid] = at
        last[pid] = at
    out = []
    for i, pid in enumerate(order):
        start = last[order[i - 1]] + 1 if i else max(0, first[pid] - 3000)
        end = first[order[i + 1]] if i + 1 < len(order) else last[pid] + 3000
        chunk = html[start:end]
        alt = re.search(r'\balt="([^"]{6,200})"', chunk) or re.search(r'\btitle="([^"]{6,200})"', chunk)
        text = _text(chunk)
        name = alt.group(1).strip() if alt else ""
        if not name:
            bits = [b.strip() for b in text.split("|") if b.strip() and not BADGE.match(b.strip())]
            name = max(bits, key=len) if bits else ""
        money = re.sub(r"save\s*(?:\|\s*)?£\s*[\d,.]+", " ", text, flags=re.IGNORECASE)
        prices = [float(x.replace(",", "")) for x in re.findall(r"£\s*([\d,]+(?:\.\d{1,2})?)", money)]
        if not name or not prices:
            continue
        price, top = min(prices), max(prices)
        out.append({"retailer": "bikesy", "id": pid, "name": name.replace("&amp;", "&"), "brand": None, "price": price,
                    "was": top if top > price else None, "available": True,
                    "url": f"{BIKESY}/outbound.php?pid={pid}"})  # fmt: skip
    return out


async def bikesy_search(client: httpx.AsyncClient, query: str, raw: bool = False) -> Any:
    """Bikesy's deals for some words, from its tag page (its search pages are closed to automated readers)."""
    html = await _get(client, "bikesy", f"{BIKESY}/tag/{_tag(query)}/", json_=False)
    return html[:3000] if raw else bikesy_deals(html)


# --- Tradeinn (Trekkinn, Bikeinn and their sister shops) -------------------------------------------------------------
# No public API: this is the search its own pages call (index.php?action=get_info_elastic_listado), a form of
# "vars[]=" query parts plus texto_search, in the shop given by id_tienda, with the delivery country in the id_pais
# cookie (the United Kingdom is 209), which is what makes it answer with a price. Unofficial, so every field is looked
# for under the names seen in use, and the currency is read from the answer when it says (else taken as euros).
TRADEINN = "https://www.tradeinn.com"
TRADEINN_SHOPS = {"diveinn": 1, "snowinn": 2, "trekkinn": 3, "bikeinn": 4, "swiminn": 6, "waveinn": 7, "runnerinn": 10,
                  "traininn": 13, "xtremeinn": 14}  # fmt: skip
TRADEINN_UK = 209
NAMES["tradeinn"] = "Tradeinn"


def _first_num(*vals: Any) -> float | None:
    for v in vals:
        n = _pounds(v)
        if n:
            return n
    return None


def tradeinn_products(data: Any, shop: str) -> list[dict[str, Any]]:
    """Products from Tradeinn's search answer (id_modelos), with the price, the full price ("rec") and stock."""
    if not isinstance(data, dict):
        raise ShopError("Tradeinn sent something other than its search answer (it may have changed its site)")
    found = data.get("id_modelos")
    if found is None and isinstance(data.get("hits"), dict):  # an Elasticsearch-shaped answer
        found = [h.get("_source") for h in data["hits"].get("hits") or [] if isinstance(h, dict)]
    out = []
    currency = str(data.get("moneda") or data.get("currency") or "EUR").upper()[:3]
    for m in found or []:
        if not isinstance(m, dict):
            continue
        name = m.get("nombre_modelo_eng") or m.get("nombre_modelo") or m.get("name")
        pid = m.get("id_modelo") or m.get("id")
        if not name or not pid:
            continue
        prods = [x for x in m.get("productes") or [] if isinstance(x, dict)]
        live = [x for x in prods if not x.get("baja")] or prods
        seller = next((s for x in live for s in x.get("sellers") or [] if isinstance(s, dict) and s.get("id_seller") == 1), None) \
            or next((s for x in live for s in x.get("sellers") or [] if isinstance(s, dict)), {})  # fmt: skip
        price = _first_num(m.get("precio_win"), seller.get("precio_producte"))
        full = _first_num(*(x.get("rec") for x in live))
        stock = any((x.get("exist") or 0) > (x.get("stock_reservat") or 0) for x in live) if any("exist" in x for x in live) else True
        slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
        brand = str(m.get("marca") or "").strip()
        full_name = f"{brand} {name}".strip() if brand and brand.lower() not in str(name).lower() else str(name).strip()
        out.append({"retailer": "tradeinn", "id": f"{shop}:{pid}", "name": full_name,
                    "brand": m.get("marca") or None, "price": price, "was": full if full and price and full > price else None,
                    "available": bool(stock), "url": f"{TRADEINN}/{shop}/en/{slug}/{pid}/p", "currency": currency})  # fmt: skip
    return out


async def tradeinn_search(client: httpx.AsyncClient, query: str, shop: str = "trekkinn", raw: bool = False) -> Any:
    """Tradeinn's products in one of its shops for some words, priced for delivery to the UK."""
    sid = TRADEINN_SHOPS.get(shop)
    if sid is None:
        raise ShopError(f"Tradeinn has no shop called {shop!r} here (choose from {', '.join(TRADEINN_SHOPS)})")
    parts = [f"id_tienda={sid}@fecha_descatalogado=[now/d-*]", "precio_win_54;asc", "48", "productos", "search", "0"]
    body = "&".join(["vars%5B%5D=" + p for p in parts] + ["texto_search=" + quote(query)])  # as its own page sends it
    try:
        r = await client.post(f"{TRADEINN}/index.php", params={"action": "get_info_elastic_listado", "id_tienda": sid, "idioma": "eng"},
                              content=body, headers={**HEADERS, "Accept": "application/json, text/javascript, */*; q=0.01",
                                                     "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                                     "Cookie": f"id_pais={TRADEINN_UK};"})  # fmt: skip
    except httpx.HTTPError as exc:
        raise ShopError(f"Tradeinn could not be reached ({type(exc).__name__})") from exc
    if r.status_code in (401, 403, 429, 503):
        raise ShopError(f"Tradeinn refused the request (HTTP {r.status_code}): it may be blocking automated requests")
    if r.status_code >= 400:
        raise ShopError(f"Tradeinn answered HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError as exc:
        page = r.text[:200].lower()
        why = "a challenge page (it is blocking automated requests)" if "captcha" in page or "challenge" in page or "cloudflare" in page \
            else "something other than JSON (it may have changed its site)"  # fmt: skip
        raise ShopError(f"Tradeinn sent {why}") from exc
    return data if raw else tradeinn_products(data, shop)

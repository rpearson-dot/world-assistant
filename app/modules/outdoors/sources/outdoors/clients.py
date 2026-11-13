"""GO Outdoors, Decathlon and Bikesy prices, read for the Outdoors list.

GO Outdoors runs on Shopify, which publishes every shop's products as JSON at standard addresses: the search
(`/search/suggest.json`) and each product (`/products/<handle>.js`, prices in pence). Decathlon does not allow its
search to be read automatically, so only a product page someone has pasted in is read, for its schema.org Product
data (the price and stock it shows search engines). Bikesy compares 30-odd UK bike shops; its search pages (/s/) are
closed to automated readers, so its tag pages (/tag/<words>/, which its robots.txt leaves open) are read instead, for
each deal's name, price, full price and Bikesy's "View Deal" link (the listing does not say which shop).
`python -m app.modules.outdoors.outdoors <words or link>` shows what each sends.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.modules.wishlist.sources.wishlist.clients import ShopError
from app.version import VERSION

GO = "https://www.gooutdoors.co.uk"
BIKESY = "https://bikesy.co.uk"
NAMES = {"gooutdoors": "GO Outdoors", "decathlon": "Decathlon", "bikesy": "Bikesy"}
HEADERS = {"User-Agent": f"WorldAssistant/{VERSION} (personal price watch, once a day)",
           "Accept-Language": "en-GB,en;q=0.9"}  # fmt: skip
URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
LDJSON = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL)


def _on(host: str, site: str) -> bool:
    """The host is the site or one of its subdomains (not a look-alike such as notsigmasports.com)."""
    return host == site or host.endswith("." + site)


def links(text: str) -> list[tuple[str, str]]:
    """(retailer, url) for each GO Outdoors, Decathlon or Sigma Sports product link in an item's description."""
    out = []
    for u in URL.findall(text or ""):
        u = u.rstrip(").,;")
        host = (urlparse(u).hostname or "").lower()
        if _on(host, "gooutdoors.co.uk") and "/products/" in u:
            out.append(("gooutdoors", u.split("?")[0].split("#")[0]))
        elif _on(host, "decathlon.co.uk"):
            out.append(("decathlon", u.split("#")[0]))
        elif _on(host, "sigmasports.com") and "/item/" in u:
            out.append(("sigma", u.split("?")[0].split("#")[0]))
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
                    "was": top if top > price else None, "available": True, "shop": shop_in(chunk),
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


# --- Bikesy: which shop a deal is at ------------------------------------------------------------------------------
# Bikesy's View Deal links hide the shop (outbound.php), but each deal names it, in the shop's logo or in words.
SHOPS = ("Sigma Sports", "Tredz", "Merlin Cycles", "Wiggle", "Chain Reaction", "Rutland Cycling", "Evans Cycles",
         "Leisure Lakes", "Halfords", "Decathlon", "Cycle Republic", "Planet X", "Ribble", "Mantel", "Bike24",
         "Bike-Discount", "Bike Components", "BikeInn", "Tradeinn", "Alltricks", "Probikekit", "Sportpursuit",
         "Cyclestore", "Westbrook Cycles", "Balfe's Bikes", "Pedal On", "Freewheel", "Condor Cycles")  # fmt: skip


def shop_in(chunk: str) -> str | None:
    """The shop a Bikesy deal names (in its text, an image's alt text or a logo's file name), if it is one known here."""
    flat = re.sub(r"[^a-z0-9]+", "", chunk.lower())
    for name in SHOPS:
        if re.sub(r"[^a-z0-9]+", "", name.lower()) in flat:
            return name
    return None


# --- Sigma Sports --------------------------------------------------------------------------------------------------
# Its own site (shops at Hampton Wick, Oakham, Letchworth and Wilmslow), with no API. A product page is read from the
# schema.org Product it may publish, else from the page itself: the name in its heading and the first pound figures
# after it (the price, then the full price when it is on offer). Its search address is not published, so the likely
# ones are tried, only those its robots.txt allows, and the first that lists products is used from then on
# (WA_OUTDOORS_SIGMA_SEARCH sets it, with {q} for the words).
SIGMA = "https://www.sigmasports.com"
NAMES["sigma"] = "Sigma Sports"
SIGMA_SEARCHES = (SIGMA + "/search?q={q}", SIGMA + "/search?query={q}", SIGMA + "/search/{q}")
SIGMA_ITEM = re.compile(r'href="((?:https://www\.sigmasports\.com)?/item/([^/"?#]+)/([^/"?#]+)/([A-Za-z0-9]+))[?#"]')
_robots: dict[str, Any] = {}
_sigma_search: dict[str, str] = {}


async def allowed(client: httpx.AsyncClient, url: str) -> bool:
    """Whether the site's robots.txt lets an automated reader (us) fetch this address. Read once per site; a site
    with no robots.txt (or one that cannot be read) allows it."""
    from urllib.robotparser import RobotFileParser

    site = "{0.scheme}://{0.netloc}".format(urlparse(url))
    if site not in _robots:
        rp = RobotFileParser()
        try:
            r = await client.get(site + "/robots.txt", headers=HEADERS, timeout=15)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except httpx.HTTPError:
            rp.parse([])
        _robots[site] = rp
    return bool(_robots[site].can_fetch(HEADERS["User-Agent"], url))


def _heading(html: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).replace("&amp;", "&").strip() if m else ""


def sigma_page(html: str, url: str) -> dict[str, Any] | None:
    """A Sigma Sports product from its page: the schema.org Product if there is one, else the heading and prices."""
    code = url.rstrip("/").split("/")[-1]
    found = _ld_products(html)
    if found:
        p = found[0]
        offers = p.get("offers") or {}
        offers = offers[0] if isinstance(offers, list) and offers else offers
        stock = str(offers.get("availability") or "")
        brand = p.get("brand")
        return {"retailer": "sigma", "id": code, "name": str(p.get("name") or "").strip() or _heading(html) or url,
                "brand": brand.get("name") if isinstance(brand, dict) else brand,
                "price": _pounds(offers.get("price") or offers.get("lowPrice")), "was": None,
                "available": "OutOfStock" not in stock and "SoldOut" not in stock, "url": url}  # fmt: skip
    name = _heading(html)
    h1 = re.search(r"</h1>", html, re.IGNORECASE)
    if not name or not h1:
        return None
    after = _text(html[h1.end():h1.end() + 6000].replace("&pound;", "£"))
    after = re.sub(r"save\s*(?:\|\s*)?£\s*[\d,.]+", " ", after, flags=re.IGNORECASE)
    prices = [float(x.replace(",", "")) for x in re.findall(r"£\s*([\d,]+(?:\.\d{1,2})?)", after)[:2]]
    if not prices:
        return None
    price = prices[0]
    was = prices[1] if len(prices) > 1 and prices[1] > price else None
    stock = after[:3000].lower()
    out = "out of stock" in stock and "in stock, available" not in stock
    return {"retailer": "sigma", "id": code, "name": name, "brand": url.split("/item/")[1].split("/")[0].replace("-", " "),
            "price": price, "was": was, "available": not out, "url": url}  # fmt: skip


async def sigma_product(client: httpx.AsyncClient, url: str, raw: bool = False) -> Any:
    """One Sigma Sports product from its page."""
    html = await _get(client, "sigma", url, json_=False)
    if raw:
        return {"ld_json": _ld_products(html), "heading": _heading(html), "start": html[:1500]}
    p = sigma_page(html, url)
    if not p:
        raise ShopError("Sigma Sports' page had no product name and price (it may have changed its site, or blocked the request)")
    return p


def sigma_listing(html: str) -> list[dict[str, Any]]:
    """The products on a Sigma Sports listing (search, category or brand page): each is the stretch of the page from
    its first /item/ link to the next product's, with the lowest pound figure as the price and the highest as the full
    price. The name is the link's text or image alt text, else made from the address."""
    html = html.replace("&pound;", "£")
    marks = [(m.start(), m) for m in SIGMA_ITEM.finditer(html)]
    order: list[str] = []
    first: dict[str, tuple[int, Any]] = {}
    for at, m in marks:
        code = m.group(4)
        if code not in first:
            order.append(code)
            first[code] = (at, m)
    out = []
    for i, code in enumerate(order):
        at, m = first[code]
        end = first[order[i + 1]][0] if i + 1 < len(order) else at + 3000
        chunk = html[at:end]
        alt = re.search(r'\balt="([^"]{6,200})"', chunk)
        brand, slug = m.group(2).replace("-", " "), m.group(3).replace("-", " ")
        name = alt.group(1).strip() if alt else f"{brand} {slug}"
        money = re.sub(r"save\s*(?:\|\s*)?£\s*[\d,.]+", " ", _text(chunk), flags=re.IGNORECASE)
        prices = [float(x.replace(",", "")) for x in re.findall(r"£\s*([\d,]+(?:\.\d{1,2})?)", money)]
        if not prices:
            continue
        price, top = min(prices), max(prices)
        href = m.group(1)
        out.append({"retailer": "sigma", "id": code, "name": name.replace("&amp;", "&"), "brand": brand, "price": price,
                    "was": top if top > price else None, "available": True,
                    "url": href if href.startswith("http") else SIGMA + href})  # fmt: skip
    return out


def _shares_a_word(products: list[dict[str, Any]], query: str) -> bool:
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
    return any(words & set(re.findall(r"[a-z0-9]+", p["name"].lower())) for p in products)


async def sigma_search(client: httpx.AsyncClient, query: str, raw: bool = False, template: str = "") -> Any:
    """Sigma Sports' products for some words, from its search page (see above for how the address is found)."""
    from urllib.parse import quote_plus

    q = quote_plus(query)
    tries = [template] if template else ([_sigma_search["at"]] if "at" in _sigma_search else list(SIGMA_SEARCHES))
    closed = answered = 0
    for t in tries:
        url = t.replace("{q}", q)
        if not await allowed(client, url):
            closed += 1
            continue
        try:
            html = await _get(client, "sigma", url, json_=False)
        except ShopError as exc:
            if "no such page" in str(exc) and not template:
                continue
            raise
        if raw:
            return {"url": url, "start": html[:2500]}
        answered += 1
        got = sigma_listing(html)
        if got and _shares_a_word(got, query) or "at" in _sigma_search or template:
            _sigma_search.setdefault("at", t)
            return got
    if closed == len(tries):
        raise ShopError("Sigma Sports' robots.txt closes its search to automated readers: paste product links instead")
    if not answered:
        raise ShopError("Sigma Sports has none of the likely search addresses (set WA_OUTDOORS_SIGMA_SEARCH, or paste "
                        "product links)")  # fmt: skip
    return []  # pages came back, none listing these words: nothing found (or not the search page; the probe shows which)

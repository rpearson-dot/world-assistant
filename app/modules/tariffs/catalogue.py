"""Octopus Energy's current tariffs for your region, from its public products API (no key): each product open to
new customers (not business, not prepay, not restricted), its tariff for your region (paid by monthly direct
debit), today's standing charge and the last 30 days of unit rates, from which `price.pattern` makes today's day.

GET /v1/products/?brand=OCTOPUS_ENERGY&is_business=false; /v1/products/{code}/ (tariffs by region, "_H");
/v1/products/{code}/electricity-tariffs/{tariff}/standard-unit-rates/.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.modules.octopus.octopus import BASE, TARIFF_RE, parse_rates

# what each kind of tariff asks of you, and what the comparison cannot count (by a fragment of the product code)
CONDITIONS: list[tuple[str, str, str | None]] = [
    ("INTELLI-FLUX", "needs solar, a battery and an inverter Octopus can control; comes with its own export", "intelligent-flux"),
    ("FLUX", "needs solar and a battery; comes with Flux export, not another export tariff", "flux"),
    ("INTELLI", ("needs a car or charger Octopus can control; the extra cheap hours it gives when it charges the car are "
                "not counted, so it may cost less than shown"), None),
    ("GO", "needs an electric car", None),
    ("COSY", "needs a heat pump", None),
    ("AGILE-OUTGOING", "the rate changes every half-hour; priced at the last 30 days' average", None),
    ("AGILE", "the rate changes every half-hour, with wholesale prices; priced at the last 30 days' average", None),
    ("SILVER", "changes each day with wholesale prices; priced at the last 30 days' average", None),
    ("TRACKER", "changes each day with wholesale prices; priced at the last 30 days' average", None),
    ("OUTGOING", "needs your solar's MCS certificate (or equivalent)", None),
]


def conditions(code: str) -> tuple[list[str], str | None]:
    """What a tariff asks of you, and the pair it belongs to, from its product code. The most specific fragment wins
    (Intelligent Flux is not also Flux and Intelligent; Agile Outgoing not also Agile and Outgoing)."""
    up, out, fam, seen = code.upper(), [], None, []
    for frag, words, pairs in CONDITIONS:
        if re.search(rf"(^|-){frag}(-|$)", up) and not any(frag in f for f in seen):
            seen.append(frag)
            out.append(words)
            fam = fam or pairs
    return out, fam


def region_of(tariff_code: str | None) -> str | None:
    m = TARIFF_RE.match((tariff_code or "").strip().upper())
    return m["region"] if m else None


def open_products(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Products a household can move to now, from one page of the products list."""
    out = []
    for p in body.get("results") or []:
        if p.get("is_business") or p.get("is_prepay") or p.get("is_restricted") or (p.get("brand") or "OCTOPUS_ENERGY") != "OCTOPUS_ENERGY":
            continue
        out.append({"code": p["code"], "name": p.get("display_name") or p.get("full_name") or p["code"],
                    "direction": "export" if (p.get("direction") or "").upper() == "EXPORT" else "import",
                    "term_months": p.get("term"), "variable": bool(p.get("is_variable")), "green": bool(p.get("is_green"))})  # fmt: skip
    return out


def regional(detail: dict[str, Any], region: str) -> dict[str, Any] | None:
    """A product's single-rate electricity tariff for a region, paid monthly by direct debit."""
    t = ((detail.get("single_register_electricity_tariffs") or {}).get(f"_{region}") or {})
    t = t.get("direct_debit_monthly") or next(iter(t.values()), None)
    if not t or not t.get("code"):
        return None
    return {"tariff": t["code"], "standing_p": t.get("standing_charge_inc_vat"),
            "exit_fee_p": t.get("exit_fees") if (t.get("exit_fees_type") or "NONE") != "NONE" else 0}  # fmt: skip


async def _pages(client: httpx.AsyncClient, url: str, params: dict[str, Any] | None, limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _ in range(limit):
        r = await client.get(url, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        out.append(body)
        url, params = body.get("next"), None
        if not url:
            break
    return out


async def rates(client: httpx.AsyncClient, product: str, tariff: str, now: datetime, days: int = 31) -> list[dict[str, Any]]:
    url = f"{BASE}/v1/products/{product}/electricity-tariffs/{tariff}/standard-unit-rates/"
    params = {"period_from": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "period_to": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "page_size": 1500}  # fmt: skip
    rows: list[dict[str, Any]] = []
    for body in await _pages(client, url, params):
        rows += parse_rates(body)
    return rows


async def octopus(client: httpx.AsyncClient, region: str, now: datetime, also: str | None = None) -> list[dict[str, Any]]:
    """Every open Octopus tariff for the region with its standing charge and recent unit rates (and `also`, your own
    product, even when it is no longer open to new customers)."""
    products = []
    for body in await _pages(client, f"{BASE}/v1/products/", {"brand": "OCTOPUS_ENERGY", "is_business": "false", "page_size": 100}):
        products += open_products(body)
    if also and all(p["code"] != also for p in products):
        products.append({"code": also, "name": also, "direction": "import"})
    out = []
    for p in products:
        try:
            r = await client.get(f"{BASE}/v1/products/{p['code']}/", timeout=30)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            detail = r.json()
            t = regional(detail, region)
            if t is None:
                continue  # no single-rate electricity tariff (gas, Economy 7 only)
            if p["name"] == p["code"]:
                p["name"] = detail.get("display_name") or p["name"]
            conds, fam = conditions(p["code"])
            out.append({**p, **t, "supplier": "Octopus Energy", "conditions": conds, "pairs_with": fam, "source": "octopus",
                        "product": p["code"], "rates": await rates(client, p["code"], t["tariff"], now)})  # fmt: skip
        except httpx.HTTPError as exc:
            out.append({**p, "error": str(exc)[:160]})
    return out

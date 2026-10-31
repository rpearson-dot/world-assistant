"""OCPI locations and tariffs made into rows, and a site's chargers summed up. Pure.

A site (Location) holds chargers (EVSEs); a charger holds one or more connectors (a CCS and a CHAdeMO lead on one
rapid charger, say), and only one of them can be used at a time, so a charger's status is its connectors' status.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

OPERATOR = "gridserve"

# OCPI EVSE statuses, and how they count. GRIDSERVE has been seen to spell some its own way ("Out_Of_Order").
WORKING = {"AVAILABLE", "CHARGING", "BLOCKED", "RESERVED"}  # can charge a car (now or when it is free)
BROKEN = {"OUT_OF_ORDER", "INOPERATIVE"}
GONE = {"REMOVED", "PLANNED"}  # not there to use: left out
STATUSES = WORKING | BROKEN | GONE | {"UNKNOWN"}

# OCPI connector standards, as people say them.
CONNECTORS = {
    "IEC_62196_T2_COMBO": "CCS", "CHADEMO": "CHAdeMO", "IEC_62196_T2": "Type 2", "IEC_62196_T1": "Type 1",
    "IEC_62196_T1_COMBO": "CCS1", "DOMESTIC_G": "3-pin", "TESLA_S": "Tesla", "TESLA_R": "Tesla",
}  # fmt: skip
# What a question names, to the connector it means.
ASKED = {"ccs": "CCS", "ccs2": "CCS", "combo": "CCS", "chademo": "CHAdeMO", "type2": "Type 2", "t2": "Type 2",
         "type 2": "Type 2", "type1": "Type 1", "type 1": "Type 1"}  # fmt: skip
# Name prefixes GRIDSERVE uses for its networks.
NETWORKS = {"EH": "Electric Highway"}


def status(raw: Any) -> str:
    s = re.sub(r"[^A-Z]+", "_", str(raw or "").upper()).strip("_")
    s = {"OUTOFORDER": "OUT_OF_ORDER", "OUT_OF_SERVICE": "OUT_OF_ORDER", "FAULTED": "OUT_OF_ORDER",
         "OCCUPIED": "CHARGING", "IN_USE": "CHARGING"}.get(s, s)  # fmt: skip
    return s if s in STATUSES else "UNKNOWN"


def connector_name(standard: Any) -> str:
    s = str(standard or "")
    return CONNECTORS.get(s.upper(), s.replace("_", " ").title() or "Unknown")


def asked_connector(text: str | None) -> str | None:
    """'chademo' -> 'CHAdeMO'; None when nothing (or nothing known) is asked for."""
    if not text:
        return None
    t = text.strip().lower()
    return ASKED.get(t) or ASKED.get(t.replace("-", "").replace(" ", "")) or next(
        (v for v in CONNECTORS.values() if v.lower() == t), None)


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _when(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return t if t.tzinfo else None


def name_and_network(name: str) -> tuple[str, str | None]:
    """'EH - MOTO Thurrock' -> ('MOTO Thurrock', 'Electric Highway'). Unknown prefixes are kept in the name."""
    m = re.match(r"^\s*([A-Z]{2,3})\s+-\s+(.+)$", name or "")
    if m and m.group(1) in NETWORKS:
        return m.group(2).strip(), NETWORKS[m.group(1)]
    return (name or "").strip(), None


def connector(c: dict[str, Any]) -> dict[str, Any]:
    watts = _float(c.get("max_electric_power"))
    if not watts:
        v, a = _float(c.get("max_voltage")), _float(c.get("max_amperage"))
        watts = v * a if v and a else None
    kind = str(c.get("power_type") or "")
    return {"type": connector_name(c.get("standard")), "dc": kind.upper() == "DC",
            "kw": round(watts / 1000, 1) if watts else None, "cable": str(c.get("format") or "").upper() == "CABLE",
            "tariffs": [str(t) for t in c.get("tariff_ids") or []]}  # fmt: skip


def evse(e: dict[str, Any], site_id: str) -> dict[str, Any] | None:
    uid = e.get("uid") or e.get("evse_id")
    st = status(e.get("status"))
    if not uid or st in GONE:
        return None
    conns = [connector(c) for c in e.get("connectors") or [] if isinstance(c, dict)]
    kws = [c["kw"] for c in conns if c["kw"]]
    return {"id": f"{OPERATOR}:{uid}", "site_id": site_id, "status": st, "connectors": conns,
            "max_kw": max(kws) if kws else None, "updated_at": _when(e.get("last_updated")),
            "detail": {k: v for k, v in e.items() if k not in ("connectors", "images")}}  # fmt: skip


def site(loc: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """A published location as (site row, charger rows); None for one not published or with no position."""
    if loc.get("publish") is False or not loc.get("id"):
        return None
    coords = loc.get("coordinates") or {}
    lat, lon = _float(coords.get("latitude")), _float(coords.get("longitude"))
    if lat is None or lon is None or not (49 <= lat <= 61.5 and -9 <= lon <= 2.5):
        return None
    sid = f"{OPERATOR}:{loc['id']}"
    name, network = name_and_network(str(loc.get("name") or loc.get("address") or loc["id"]))
    hours = loc.get("opening_times") or {}
    chargers = [x for x in (evse(e, sid) for e in loc.get("evses") or [] if isinstance(e, dict)) if x]
    row = {"id": sid, "operator": OPERATOR, "name": name, "network": network, "address": loc.get("address"),
           "city": loc.get("city"), "postcode": loc.get("postal_code"), "lat": lat, "lon": lon,
           "facilities": [str(f) for f in loc.get("facilities") or []],
           "open_24h": hours.get("twentyfourseven") if isinstance(hours, dict) else None,
           "opening": hours if isinstance(hours, dict) else {}, "updated_at": _when(loc.get("last_updated")),
           "detail": {k: v for k, v in loc.items() if k not in ("evses", "operator", "suboperator", "owner", "images")}}  # fmt: skip
    return row, chargers


def tariff(t: dict[str, Any]) -> dict[str, Any] | None:
    """A tariff's energy price in pence per kWh with VAT (OCPI prices are without it). Other parts (a time or
    parking fee) are kept in `detail`."""
    if not t.get("id"):
        return None
    price = None
    for el in t.get("elements") or []:
        for pc in (el or {}).get("price_components") or []:
            if str(pc.get("type") or "").upper() == "ENERGY" and _float(pc.get("price")) is not None:
                p = _float(pc["price"]) * (1 + (_float(pc.get("vat")) or 0) / 100) * 100
                price = p if price is None else min(price, p)
    return {"id": f"{OPERATOR}:{t['id']}", "currency": t.get("currency"),
            "pence_per_kwh": round(price, 1) if price is not None else None, "detail": t}  # fmt: skip


# --- a site summed up -------------------------------------------------------------------------------------------------

BUSY = {"CHARGING", "BLOCKED", "RESERVED"}


def unit_of(evse_id: str) -> str:
    """The charger (the box in the car park) an EVSE belongs to. GRIDSERVE lists each lead as its own EVSE, numbered
    on the charger's id: GB-GRD-EGS10343-1 (CCS), -2 (CHAdeMO) and -3 (Type 2) are one charger, which charges one car
    at a time on its rapid leads."""
    return re.sub(r"-\d{1,2}$", "", evse_id)


def summary(chargers: list[dict[str, Any]], prices: dict[str, float], want: str | None = None) -> dict[str, Any]:
    """How many chargers (boxes) there are, how many work and are free now, the same by connector, the fastest and
    the price range. A charger is in use when any of its leads is; free when none is and one is available. With `want`
    (a connector name), only the chargers with that connector, and only that connector, count."""
    units: dict[str, list[dict[str, Any]]] = {}
    for c in chargers:
        units.setdefault(unit_of(c["id"]), []).append(c)
    by: dict[str, dict[str, Any]] = {}
    pence: list[float] = []
    counts = {"total": 0, "working": 0, "free": 0, "in_use": 0, "out_of_order": 0, "unknown": 0}
    kws: list[float] = []
    for leads in units.values():
        busy = any(c["status"] in BUSY for c in leads)
        mine = [c for c in leads if not want or any(x["type"] == want for x in c["connectors"])]
        if not mine:
            continue
        st = {c["status"] for c in mine}
        counts["total"] += 1
        counts["working"] += bool(st & WORKING)
        counts["in_use"] += busy
        counts["free"] += not busy and "AVAILABLE" in st
        counts["out_of_order"] += not (st & WORKING) and bool(st & BROKEN)
        counts["unknown"] += st == {"UNKNOWN"}
        for c in mine:
            for x in c["connectors"]:
                if want and x["type"] != want:
                    continue
                b = by.setdefault(x["type"], {"total": 0, "working": 0, "free": 0, "max_kw": None})
                b["total"] += 1
                b["working"] += c["status"] in WORKING
                b["free"] += c["status"] == "AVAILABLE" and not busy
                if x["kw"]:
                    kws.append(x["kw"])
                    b["max_kw"] = max(b["max_kw"] or 0, x["kw"])
                pence += [prices[t] for t in x["tariffs"] if prices.get(t) is not None]
    return {**counts, "max_kw": max(kws) if kws else None, "connectors": dict(sorted(by.items())),
            "pence_per_kwh": {"min": min(pence), "max": max(pence)} if pence else None}  # fmt: skip


def sentence(name: str, s: dict[str, Any], km: float | None = None, want: str | None = None) -> str:
    """'MOTO Thurrock: 22 of 26 chargers working, 15 free now, up to 350 kW, 79p/kWh, 3.2 km away.'"""
    what = f"{want} chargers" if want else "chargers"
    if not s["total"]:
        return f"{name}: no {what}."
    bits = [f"{s['working']} of {s['total']} {what} working", f"{s['free']} free now"]
    if s["max_kw"]:
        bits.append(f"up to {s['max_kw']:g} kW")
    p = s["pence_per_kwh"]
    if p:
        bits.append(f"{p['min']:.0f}p/kWh" if round(p["min"]) == round(p["max"]) else f"{p['min']:.0f}-{p['max']:.0f}p/kWh")
    if km is not None:
        bits.append(f"{km:.1f} km away")
    return f"{name}: " + ", ".join(bits) + "."

"""Value cleaning for Fuel Finder data. Pure functions, no I/O.

Record layouts in the JSON are not yet confirmed, so `get_path` accepts either
nested objects or flat dotted keys, with or without a leading "forecourts.".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

MIN_PENCE = 50.0
MAX_PENCE = 400.0
GRADES = ("E5", "E10", "B7S", "B7P", "B10", "HVO")

_JS_TIME = re.compile(r"^[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) (\d{4}) (\d{2}):(\d{2}):(\d{2}) GMT([+-]\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}  # fmt: skip


def get_path(record: dict[str, Any], dotted: str) -> Any:
    """Look up 'location.latitude' as nested keys or as a flat dotted key."""
    for key in (dotted, f"forecourts.{dotted}"):
        if key in record:
            return record[key]
    node: Any = record
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            node = None
            break
        node = node[part]
    return node


def to_pence(value: Any) -> float | None:
    """Pence per litre from a price that may be pence or pounds. None if implausible."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0 < number < 10:  # pounds per litre
        number *= 100
    if not MIN_PENCE <= number <= MAX_PENCE:
        return None
    return round(number, 1)


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().upper()
        if v == "TRUE":
            return True
        if v == "FALSE":
            return False
    return None


def parse_timestamp(value: Any) -> datetime | None:
    """Parse the JS-style and ISO-ish forms the feed uses. Returns an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    m = _JS_TIME.match(text)
    if m:
        mon, day, year, hh, mm, ss, off = m.groups()
        try:
            dt = datetime(int(year), _MONTHS[mon], int(day), int(hh), int(mm), int(ss),
                          tzinfo=UTC)  # fmt: skip
        except (KeyError, ValueError):
            return None
        sign = 1 if off[0] == "+" else -1
        delta = sign * (int(off[1:3]) * 3600 + int(off[3:5]) * 60)
        return datetime.fromtimestamp(dt.timestamp() - delta, UTC)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---- Records, confirmed against a live pull (8,140 stations, 17 batches) ----------------

# Live fuel codes. The CSV guide uses short forms (B7S, B7P); accept them as aliases.
FUEL_CODES = ("E10", "E5", "B7_STANDARD", "B7_PREMIUM", "B10", "HVO")
FUEL_ALIASES = {"B7S": "B7_STANDARD", "B7P": "B7_PREMIUM", "DIESEL": "B7_STANDARD",
                "PETROL": "E10", "UNLEADED": "E10", "SUPER": "E5"}  # fmt: skip
# Loose UK bounding box (lat, lon). Anything outside has bad coordinates.
UK_LAT = (49.8, 60.9)
UK_LON = (-8.7, 1.8)


def canonical_fuel(value: str) -> str | None:
    code = value.strip().upper().replace(" ", "_")
    code = FUEL_ALIASES.get(code, code)
    return code if code in FUEL_CODES else None


@dataclass(frozen=True)
class Station:
    node_id: str
    trading_name: str | None
    brand_name: str | None
    is_motorway: bool
    is_supermarket: bool
    temporary_closure: bool
    permanent_closure: bool
    phone: str | None
    address_line_1: str | None
    city: str | None
    county: str | None
    country: str | None
    postcode: str | None
    lat: float | None
    lon: float | None
    coords_ok: bool
    amenities: tuple[str, ...]
    fuel_types: tuple[str, ...]
    opening_times: dict[str, Any] | None

    @property
    def open_for_business(self) -> bool:
        return not (self.temporary_closure or self.permanent_closure)


@dataclass(frozen=True)
class PriceRow:
    node_id: str
    fuel_type: str
    pence: float
    last_updated: datetime | None
    effective: datetime | None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def coords_valid(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return UK_LAT[0] <= lat <= UK_LAT[1] and UK_LON[0] <= lon <= UK_LON[1]


def parse_station(rec: dict[str, Any]) -> Station | None:
    node_id = rec.get("node_id")
    if not isinstance(node_id, str) or not node_id:
        return None
    loc = rec.get("location") or {}
    lat, lon = _float(loc.get("latitude")), _float(loc.get("longitude"))
    return Station(
        node_id=node_id,
        trading_name=rec.get("trading_name"),
        brand_name=rec.get("brand_name"),
        is_motorway=rec.get("is_motorway_service_station") is True,
        is_supermarket=rec.get("is_supermarket_service_station") is True,
        temporary_closure=rec.get("temporary_closure") is True,
        permanent_closure=rec.get("permanent_closure") is True,
        phone=rec.get("public_phone_number"),
        address_line_1=loc.get("address_line_1"),
        city=loc.get("city"),
        county=loc.get("county") or None,
        country=loc.get("country"),
        postcode=loc.get("postcode"),
        lat=lat,
        lon=lon,
        coords_ok=coords_valid(lat, lon),
        amenities=tuple(a for a in rec.get("amenities") or [] if isinstance(a, str)),
        fuel_types=tuple(
            c for t in rec.get("fuel_types") or [] if isinstance(t, str) and (c := canonical_fuel(t))
        ),
        opening_times=rec.get("opening_times") if isinstance(rec.get("opening_times"), dict) else None,
    )


def parse_prices(rec: dict[str, Any]) -> list[PriceRow]:
    """Valid, plausible prices from one price record. Bad or odd values are dropped."""
    node_id = rec.get("node_id")
    if not isinstance(node_id, str) or not node_id:
        return []
    out: dict[str, PriceRow] = {}
    for fp in rec.get("fuel_prices") or []:
        if not isinstance(fp, dict) or not isinstance(fp.get("fuel_type"), str):
            continue
        fuel = canonical_fuel(fp["fuel_type"])
        pence = to_pence(fp.get("price"))
        if fuel is None or pence is None:
            continue
        out[fuel] = PriceRow(
            node_id, fuel, pence,
            parse_timestamp(fp.get("price_last_updated")),
            parse_timestamp(fp.get("price_change_effective_timestamp")),
        )  # fmt: skip
    return list(out.values())

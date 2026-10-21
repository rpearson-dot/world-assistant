"""Find the cheapest fuel near a point or along a corridor."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.integrations.fuelfinder.normalise import canonical_fuel
from app.integrations.nationalhighways.match import Corridor, distance_to_line_m

EARTH_KM = 6371.0088


@dataclass(frozen=True)
class FuelHit:
    node_id: str
    name: str | None
    brand: str | None
    postcode: str | None
    city: str | None
    lat: float
    lon: float
    fuel: str
    pence: float
    price_age_hours: float | None
    distance_km: float  # from the point, or from the corridor centreline
    motorway_services: bool
    supermarket: bool

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["distance_km"] = round(self.distance_km, 2)
        if self.price_age_hours is not None:
            d["price_age_hours"] = round(self.price_age_hours, 1)
        return d


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


_SELECT = text(
    """
    SELECT s.node_id, s.trading_name, s.brand_name, s.postcode, s.city, s.lat, s.lon,
           s.is_motorway, s.is_supermarket, p.pence, p.last_updated, p.effective
    FROM fuel_station s JOIN fuel_price p ON p.node_id = s.node_id
    WHERE p.fuel_type = :fuel AND s.coords_ok AND NOT s.temporary_closure
      AND NOT s.permanent_closure
      AND s.lat BETWEEN :lat0 AND :lat1 AND s.lon BETWEEN :lon0 AND :lon1
      AND (CAST(:max_age_h AS float) IS NULL
           OR COALESCE(p.last_updated, p.effective) > now() - CAST(:max_age_h AS float) * interval '1 hour')
    """
)


def _hit(r: Any, fuel: str, distance_km: float, now: datetime) -> FuelHit:
    stamp = r["last_updated"] or r["effective"]
    age = (now - stamp).total_seconds() / 3600 if stamp else None
    return FuelHit(
        r["node_id"], r["trading_name"], pretty_brand(r["brand_name"]), r["postcode"], r["city"],
        r["lat"], r["lon"], fuel, float(r["pence"]), age, distance_km,
        bool(r["is_motorway"]),
        bool(r["is_supermarket"]) or brand_key(r["brand_name"]) in SUPERMARKET_BRANDS,
    )  # fmt: skip


async def _candidates(
    engine: AsyncEngine, fuel: str, box: tuple[float, float, float, float], max_age_h: float | None
) -> Sequence[Any]:
    async with engine.connect() as conn:
        result = await conn.execute(
            _SELECT,
            {"fuel": fuel, "lat0": box[0], "lon0": box[1], "lat1": box[2], "lon1": box[3],
             "max_age_h": max_age_h},
        )  # fmt: skip
        return result.mappings().all()


async def all_stations(
    engine: AsyncEngine, fuel: str, max_age_h: float | None = 48.0
) -> list[dict[str, Any]]:
    """Every open station with a current price for `fuel`, nationwide, without any home point."""
    rows = await _candidates(engine, fuel, (49.5, -8.7, 61.0, 2.0), max_age_h)
    now = datetime.now(UTC)
    out = []
    for r in rows:
        hit = _hit(r, fuel, 0.0, now)
        out.append({"id": hit.node_id, "name": hit.name, "brand": hit.brand,
                    "postcode": hit.postcode, "lat": round(hit.lat, 5), "lon": round(hit.lon, 5),
                    "pence": hit.pence, "motorway": hit.motorway_services, "supermarket": hit.supermarket,
                    "age_h": None if hit.price_age_hours is None else round(hit.price_age_hours, 1)})  # fmt: skip
    return out


def brand_key(brand: str | None) -> str:
    """Case, spacing and apostrophes don't matter: SAINSBURYS, Sainsbury's and sainsburys are one."""
    return re.sub(r"[\u2019']", "", (brand or "").strip().casefold())


SUPERMARKET_BRANDS = frozenset(
    {"tesco", "sainsburys", "asda", "morrisons", "waitrose", "costco", "coop", "co-op", "aldi", "lidl"}
)
_PRETTY = {"sainsburys": "Sainsbury's", "bp": "BP", "eg on the move": "EG On The Move"}


def pretty_brand(brand: str | None) -> str | None:
    """The feed shouts some brands (SAINSBURYS) and not others (Texaco); show them evenly."""
    if not brand or not brand.strip():
        return brand
    key = brand_key(brand)
    if key in _PRETTY:
        return _PRETTY[key]
    raw = brand.strip()
    return raw.title() if raw.isupper() or raw.islower() else raw


def select_brands(
    hits: Sequence[FuelHit], brands: Sequence[str] = (), supermarkets: bool = False
) -> list[FuelHit]:
    """Keep stations of any named brand (case-insensitive) and/or any supermarket."""
    wanted = {brand_key(b) for b in brands if brand_key(b)}
    if not wanted and not supermarkets:
        return list(hits)
    return [h for h in hits if brand_key(h.brand) in wanted or (supermarkets and h.supermarket)]


def brand_options(hits: Sequence[FuelHit]) -> list[dict[str, Any]]:
    """Brands present in the results, with how many stations and the cheapest price."""
    seen: dict[str, dict[str, Any]] = {}
    for h in hits:
        key = brand_key(h.brand) or "(unbranded)"
        row = seen.setdefault(
            key,
            {"brand": h.brand or "(unbranded)", "stations": 0, "cheapest_pence": h.pence,
             "supermarket": h.supermarket},
        )  # fmt: skip
        row["stations"] += 1
        row["cheapest_pence"] = min(row["cheapest_pence"], h.pence)
        row["supermarket"] = row["supermarket"] or h.supermarket
    return sorted(seen.values(), key=lambda r: (-r["stations"], r["brand"].casefold()))


def compare_cheapest(
    all_hits: Sequence[FuelHit], chosen: Sequence[FuelHit], fill_litres: float
) -> dict[str, Any] | None:
    """Absolute cheapest versus cheapest among the chosen brands."""
    if not all_hits or not chosen:
        return None
    best = min(all_hits, key=lambda h: (h.pence, h.distance_km))
    pick = min(chosen, key=lambda h: (h.pence, h.distance_km))
    extra = round(pick.pence - best.pence, 1)
    return {
        "cheapest_overall": best.as_dict(),
        "cheapest_selected": pick.as_dict(),
        "extra_pence_per_litre": extra,
        "extra_cost_pounds": round(extra * fill_litres / 100, 2),
        "fill_litres": fill_litres,
        "distance_saved_km": round(best.distance_km - pick.distance_km, 2),
        "same_station": best.node_id == pick.node_id,
    }


def _rank(hits: list[FuelHit], limit: int) -> list[FuelHit]:
    return sorted(hits, key=lambda h: (h.pence, h.distance_km))[:limit]


def _fuel(code: str) -> str:
    fuel = canonical_fuel(code)
    if fuel is None:
        raise ValueError(f"unknown fuel type {code!r}")
    return fuel


async def near_point(
    engine: AsyncEngine, lat: float, lon: float, *, radius_km: float = 5.0, fuel: str = "E10",
    limit: int = 10, max_age_hours: float | None = 48.0,
) -> list[FuelHit]:  # fmt: skip
    """Cheapest stations within radius_km of a point."""
    code = _fuel(fuel)
    dlat = math.degrees(radius_km / EARTH_KM)
    dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
    rows = await _candidates(
        engine, code, (lat - dlat, lon - dlon, lat + dlat, lon + dlon), max_age_hours
    )
    now = datetime.now(UTC)
    hits = []
    for r in rows:
        d = haversine_km((lat, lon), (r["lat"], r["lon"]))
        if d <= radius_km:
            hits.append(_hit(r, code, d, now))
    return _rank(hits, limit)


async def along_corridor(
    engine: AsyncEngine, corridor: Corridor, *, within_km: float = 2.0, fuel: str = "E10",
    limit: int = 10, max_age_hours: float | None = 48.0,
) -> list[FuelHit]:  # fmt: skip
    """Cheapest stations within within_km of the corridor centreline."""
    code = _fuel(fuel)
    rows = await _candidates(
        engine, code, corridor.bbox(margin_m=within_km * 1000 - corridor.buffer_m), max_age_hours
    )
    now = datetime.now(UTC)
    hits = []
    for r in rows:
        d = distance_to_line_m((r["lat"], r["lon"]), corridor.centreline) / 1000
        if d <= within_km:
            hits.append(_hit(r, code, d, now))
    return _rank(hits, limit)

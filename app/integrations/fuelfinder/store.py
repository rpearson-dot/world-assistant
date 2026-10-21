"""Postgres storage for Fuel Finder stations and prices."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.integrations.fuelfinder.normalise import PriceRow, Station

_STATION_UPSERT = text(
    """
    INSERT INTO fuel_station
      (node_id, trading_name, brand_name, is_motorway, is_supermarket, temporary_closure,
       permanent_closure, phone, address_line_1, city, county, country, postcode, lat, lon,
       coords_ok, amenities, fuel_types, opening_times, last_seen)
    VALUES
      (:node_id, :trading_name, :brand_name, :is_motorway, :is_supermarket, :temporary_closure,
       :permanent_closure, :phone, :address_line_1, :city, :county, :country, :postcode, :lat,
       :lon, :coords_ok, :amenities, :fuel_types, CAST(:opening_times AS jsonb), :seen)
    ON CONFLICT (node_id) DO UPDATE SET
      trading_name = EXCLUDED.trading_name, brand_name = EXCLUDED.brand_name,
      is_motorway = EXCLUDED.is_motorway, is_supermarket = EXCLUDED.is_supermarket,
      temporary_closure = EXCLUDED.temporary_closure,
      permanent_closure = EXCLUDED.permanent_closure, phone = EXCLUDED.phone,
      address_line_1 = EXCLUDED.address_line_1, city = EXCLUDED.city, county = EXCLUDED.county,
      country = EXCLUDED.country, postcode = EXCLUDED.postcode, lat = EXCLUDED.lat,
      lon = EXCLUDED.lon, coords_ok = EXCLUDED.coords_ok, amenities = EXCLUDED.amenities,
      fuel_types = EXCLUDED.fuel_types, opening_times = EXCLUDED.opening_times,
      last_seen = EXCLUDED.last_seen
    """
)

CHUNK = 1000


def _chunks[T](items: Sequence[T], size: int = CHUNK) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def save_stations(engine: AsyncEngine, stations: Sequence[Station], seen: datetime) -> int:
    rows = [
        {
            "node_id": s.node_id, "trading_name": s.trading_name, "brand_name": s.brand_name,
            "is_motorway": s.is_motorway, "is_supermarket": s.is_supermarket,
            "temporary_closure": s.temporary_closure, "permanent_closure": s.permanent_closure,
            "phone": s.phone, "address_line_1": s.address_line_1, "city": s.city,
            "county": s.county, "country": s.country, "postcode": s.postcode,
            "lat": s.lat, "lon": s.lon, "coords_ok": s.coords_ok,
            "amenities": list(s.amenities), "fuel_types": list(s.fuel_types),
            "opening_times": json.dumps(s.opening_times) if s.opening_times is not None else None,
            "seen": seen,
        }
        for s in stations
    ]  # fmt: skip
    async with engine.begin() as conn:
        for chunk in _chunks(rows):
            await conn.execute(_STATION_UPSERT, list(chunk))
    return len(rows)


async def apply_prices(
    engine: AsyncEngine, prices: Sequence[PriceRow], observed_at: datetime
) -> dict[str, int]:
    """Make the stored prices equal this complete pull, logging only real changes.

    Rows for stations we don't know are skipped; a price missing from the pull is
    removed (the station stopped selling that grade).
    """
    async with engine.begin() as conn:
        known = {r[0] for r in await conn.execute(text("SELECT node_id FROM fuel_station"))}
        current = {
            (r[0], r[1]): (r[2], r[3])
            for r in await conn.execute(
                text("SELECT node_id, fuel_type, pence, effective FROM fuel_price")
            )
        }
        incoming = {(p.node_id, p.fuel_type): p for p in prices if p.node_id in known}
        changed = [
            p for key, p in incoming.items()
            if key not in current or current[key] != (p.pence, p.effective)
        ]  # fmt: skip
        gone = [key for key in current if key not in incoming]
        rows = [
            {"node_id": p.node_id, "fuel_type": p.fuel_type, "pence": p.pence,
             "last_updated": p.last_updated, "effective": p.effective, "observed_at": observed_at}
            for p in changed
        ]  # fmt: skip
        for chunk in _chunks(rows):
            await conn.execute(
                text(
                    """
                    INSERT INTO fuel_price (node_id, fuel_type, pence, last_updated, effective,
                                            observed_at)
                    VALUES (:node_id, :fuel_type, :pence, :last_updated, :effective, :observed_at)
                    ON CONFLICT (node_id, fuel_type) DO UPDATE SET pence = EXCLUDED.pence,
                      last_updated = EXCLUDED.last_updated, effective = EXCLUDED.effective,
                      observed_at = EXCLUDED.observed_at
                    """
                ),
                list(chunk),
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO fuel_price_change (node_id, fuel_type, pence, effective, observed_at)
                    VALUES (:node_id, :fuel_type, :pence, :effective, :observed_at)
                    """
                ),
                list(chunk),
            )
        for chunk in _chunks(gone):
            await conn.execute(
                text("DELETE FROM fuel_price WHERE node_id = :n AND fuel_type = :f"),
                [{"n": n, "f": f} for n, f in chunk],
            )
    return {"incoming": len(incoming), "changed": len(rows), "removed": len(gone),
            "unknown_station": len(prices) - len(incoming)}  # fmt: skip

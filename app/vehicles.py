"""Vehicles, trip running cost, and detour maths (is a cheaper station worth the trip?)."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text

from app.auth import require_token
from app.integrations.fuelfinder.normalise import canonical_fuel
from app.integrations.fuelfinder.queries import FuelHit, along_corridor
from app.integrations.nationalhighways.store import load_corridors
from app.places import check_slug

router = APIRouter(prefix="/v1", tags=["vehicles"], dependencies=[Depends(require_token)])

KM_PER_MILE = 1.609344
LITRES_PER_UK_GALLON = 4.54609


def litres_for(km: float, mpg: float) -> float:
    """Litres used over km at mpg (UK gallons)."""
    return km / KM_PER_MILE / mpg * LITRES_PER_UK_GALLON


def net_saving_pence(
    hit: FuelHit, reference_pence: float, mpg: float, fill_litres: float
) -> dict[str, float]:
    """Saving from filling here instead of at the reference price, after the detour.

    The detour is out and back, so twice the distance from the route. Everything is
    in pence; fill_litres is how much you would buy.
    """
    detour_km = 2 * hit.distance_km
    detour_cost = litres_for(detour_km, mpg) * hit.pence
    gross = (reference_pence - hit.pence) * fill_litres
    return {"detour_km": round(detour_km, 1), "detour_cost_p": round(detour_cost, 1),
            "gross_saving_p": round(gross, 1), "net_saving_p": round(gross - detour_cost, 1)}  # fmt: skip


RESERVE = 0.10  # an EV trip should finish with at least this much battery left


def usable_kwh(battery_kwh: float, soh_percent: float) -> float:
    return battery_kwh * soh_percent / 100


def ev_kwh_for(km: float, miles_per_kwh: float) -> float:
    return km / KM_PER_MILE / miles_per_kwh


# What a vehicle runs on, as people say it, and the fuel codes each allows (first is the default).
# Petrol and diesel grades are the ones Fuel Finder reports; LPG is not in it, so it takes your own price.
TYPES: dict[str, tuple[str, ...]] = {
    "petrol": ("E10", "E5"),
    "diesel": ("B7_STANDARD", "B7_PREMIUM", "B10", "HVO"),
    "lpg": ("LPG",),
}
NO_STATION_PRICES = {"LPG"}


def type_of(kind: str, fuel: str | None) -> str:
    """electric, petrol, diesel or lpg, from what is stored (kind fuel|ev and a fuel code)."""
    if kind == "ev":
        return "electric"
    return next((t for t, codes in TYPES.items() if fuel in codes), "petrol")


class VehicleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    # electric, petrol, diesel or lpg; "ev" and "fuel" (the earlier two) are still accepted
    kind: str = Field("fuel", pattern="^(fuel|ev|electric|petrol|diesel|lpg)$")
    # petrol, diesel and LPG vehicles
    fuel: str | None = None
    mpg: float | None = Field(default=None, gt=5, lt=150, description="real-world miles per UK gallon")
    tank_litres: float | None = Field(default=None, gt=5, lt=300)
    fuel_pence_per_litre: float | None = Field(default=None, gt=10, lt=400, description="your own price (LPG)")
    # electric vehicles
    battery_kwh: float | None = Field(default=None, gt=5, lt=300)
    soh_percent: float = Field(100.0, gt=30, le=100, description="battery state of health")
    miles_per_kwh: float | None = Field(default=None, gt=0.5, lt=10, description="real-world")
    home_pence_per_kwh: float | None = Field(default=None, gt=0, lt=200)

    @field_validator("fuel")
    @classmethod
    def _fuel(cls, v: str | None) -> str | None:
        if v is None or v.strip().upper() == "LPG":
            return None if v is None else "LPG"
        code = canonical_fuel(v)
        if code is None:
            raise ValueError(f"unknown fuel type {v!r}")
        return code

    @model_validator(mode="after")
    def _fields_for_kind(self) -> VehicleIn:
        if self.kind in ("ev", "electric"):
            self.kind = "ev"
            if self.battery_kwh is None or self.miles_per_kwh is None:
                raise ValueError("an electric vehicle needs battery_kwh and miles_per_kwh")
            return self
        wanted = self.kind if self.kind in TYPES else None  # "fuel": the type follows the fuel code
        self.kind = "fuel"
        if wanted:
            self.fuel = self.fuel or TYPES[wanted][0]
            if self.fuel not in TYPES[wanted]:
                raise ValueError(f"{self.fuel} is not a {wanted} fuel (choose from {', '.join(TYPES[wanted])})")
        else:
            self.fuel = self.fuel or "E10"
        if self.mpg is None:
            raise ValueError("a petrol, diesel or LPG vehicle needs mpg")
        return self


_COLS = ("id, name, kind, fuel, mpg, tank_litres, fuel_pence_per_litre, battery_kwh, soh_percent, miles_per_kwh, "
         "home_pence_per_kwh")  # fmt: skip


def _shown(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "type": type_of(row["kind"], row.get("fuel"))}


async def cheapest_home_rate(engine: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """The cheapest electricity rate on the Energy tab: the lowest in the next 48 hours, else in the past week.

    What charging at home costs when you charge at the cheap time. None when no rates are stored (the
    Energy module is off or has no key). A negative Agile price counts as free, not as being paid."""
    from app.energy import store as energy_store

    now = now or datetime.now(UTC)
    try:
        tariff, periods = await energy_store.load(engine, now)
    except Exception:  # noqa: BLE001 - no energy data is a normal state, not an error
        return None
    for start, end, span in ((now, now + timedelta(hours=48), "next 48 hours"), (now - timedelta(days=7), now, "past week")):
        within = [p for p in periods if p.end > start and p.start < end]
        if within:
            best = min(within, key=lambda p: p.pence)
            return {"pence_per_kwh": round(max(best.pence, 0.0), 2), "tariff": tariff, "over": span,
                    "from": best.start.isoformat(), "to": best.end.isoformat()}  # fmt: skip
    return None


@router.get("/vehicles/home-rate")
async def home_rate(request: Request) -> dict[str, Any]:
    """The rate an electric vehicle's trip cost uses when no home rate is given: the Energy tab's cheapest."""
    return {"cheapest": await cheapest_home_rate(request.app.state.engine)}


@router.get("/vehicles")
async def list_vehicles(request: Request) -> list[dict[str, Any]]:
    async with request.app.state.engine.connect() as conn:
        rows = await conn.execute(
            text(f"SELECT {_COLS} FROM vehicle ORDER BY name")
        )
        return [_shown(dict(r._mapping)) for r in rows]


@router.put("/vehicles/{vehicle_id}")
async def put_vehicle(vehicle_id: str, body: VehicleIn, request: Request) -> dict[str, Any]:
    check_slug(vehicle_id)
    async with request.app.state.engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO vehicle (id, name, kind, fuel, mpg, tank_litres, fuel_pence_per_litre, battery_kwh,
                                     soh_percent, miles_per_kwh, home_pence_per_kwh)
                VALUES (:id, :name, :kind, :fuel, :mpg, :tank_litres, :fuel_pence_per_litre, :battery_kwh,
                        :soh_percent, :miles_per_kwh, :home_pence_per_kwh)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind,
                  fuel = EXCLUDED.fuel, mpg = EXCLUDED.mpg, tank_litres = EXCLUDED.tank_litres,
                  fuel_pence_per_litre = EXCLUDED.fuel_pence_per_litre,
                  battery_kwh = EXCLUDED.battery_kwh, soh_percent = EXCLUDED.soh_percent,
                  miles_per_kwh = EXCLUDED.miles_per_kwh,
                  home_pence_per_kwh = EXCLUDED.home_pence_per_kwh
                """
            ),
            {"id": vehicle_id, **body.model_dump()},
        )
    return _shown({"id": vehicle_id, **body.model_dump()})


@router.delete("/vehicles/{vehicle_id}", status_code=204)
async def delete_vehicle(vehicle_id: str, request: Request) -> None:
    async with request.app.state.engine.begin() as conn:
        result = await conn.execute(text("DELETE FROM vehicle WHERE id = :id"), {"id": vehicle_id})
    if not result.rowcount:
        raise HTTPException(404, "no such vehicle")


async def get_vehicle(request: Request, vehicle_id: str) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        row = (
            await conn.execute(
                text(f"SELECT {_COLS} FROM vehicle WHERE id = :id"),
                {"id": vehicle_id},
            )
        ).mappings().first()
    if row is None:
        raise HTTPException(404, f"no vehicle {vehicle_id!r}")
    return dict(row)


@router.get("/trips/{trip_id}/cost")
async def trip_cost(
    trip_id: str,
    request: Request,
    vehicle: str,
    within_km: float = Query(2.0, gt=0, le=20),
) -> dict[str, Any]:
    """Fuel needed and cost for a routed trip, at typical and cheapest prices on the route."""
    v = await get_vehicle(request, vehicle)
    async with request.app.state.engine.connect() as conn:
        trip = (
            await conn.execute(
                text("SELECT name, corridor_id, distance_km, duration_s FROM trip WHERE id = :id"),
                {"id": trip_id},
            )
        ).mappings().first()
    if trip is None:
        raise HTTPException(404, f"no trip {trip_id!r}")
    if trip["corridor_id"] is None or trip["distance_km"] is None:
        raise HTTPException(409, "this trip has no route yet; press Build route on /places")
    if v["kind"] == "ev":
        cheapest = None if v["home_pence_per_kwh"] else await cheapest_home_rate(request.app.state.engine)
        return _ev_trip_cost(trip, v, cheapest)
    engine = request.app.state.engine
    hits: list[FuelHit] = []
    if v["fuel"] not in NO_STATION_PRICES:
        corridor = next(c for c in await load_corridors(engine, only_enabled=False)
                        if c.id == trip["corridor_id"])  # fmt: skip
        hits = await along_corridor(engine, corridor, within_km=within_km, fuel=v["fuel"], limit=10_000)
    litres = litres_for(trip["distance_km"], v["mpg"])
    out: dict[str, Any] = {
        "trip": trip["name"], "vehicle": v["name"], "fuel": v["fuel"], "type": type_of(v["kind"], v["fuel"]),
        "distance_km": round(trip["distance_km"], 1), "litres": round(litres, 1),
        "base_minutes": round(trip["duration_s"] / 60), "stations_considered": len(hits),
    }  # fmt: skip
    own = v.get("fuel_pence_per_litre")
    if own:
        out["own_pence_per_litre"] = own
        out["cost_gbp_at_own_price"] = round(litres * own / 100, 2)
    elif v["fuel"] in NO_STATION_PRICES:
        out["note"] = "No station prices are published for LPG: give the vehicle your own price per litre."
    if hits:
        typical = statistics.median(h.pence for h in hits)
        cheapest = min(h.pence for h in hits)
        out["typical_pence_per_litre"] = round(typical, 1)
        out["cheapest_pence_per_litre"] = round(cheapest, 1)
        out["cost_gbp_typical"] = round(litres * typical / 100, 2)
        out["cost_gbp_cheapest"] = round(litres * cheapest / 100, 2)
    return out


def _ev_trip_cost(trip: Any, v: dict[str, Any], cheapest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Energy, range check and home-charge cost. No public charging prices are available."""
    kwh = ev_kwh_for(trip["distance_km"], v["miles_per_kwh"])
    usable = usable_kwh(v["battery_kwh"], v["soh_percent"])
    out: dict[str, Any] = {
        "trip": trip["name"], "vehicle": v["name"], "kind": "ev",
        "distance_km": round(trip["distance_km"], 1), "kwh": round(kwh, 1),
        "usable_battery_kwh": round(usable, 1),
        "range_km_full": round(usable * v["miles_per_kwh"] * KM_PER_MILE),
        "base_minutes": round(trip["duration_s"] / 60),
        "one_way_on_a_full_charge": kwh <= usable * (1 - RESERVE),
        "return_on_a_full_charge": 2 * kwh <= usable * (1 - RESERVE),
        "reserve_percent": round(RESERVE * 100),
    }  # fmt: skip
    rate, source = (v["home_pence_per_kwh"], "yours") if v["home_pence_per_kwh"] else (
        (cheapest["pence_per_kwh"], "the Energy tab's cheapest rate") if cheapest else (None, None))
    if rate is not None:
        out["home_pence_per_kwh"] = rate
        out["home_rate_from"] = source
        out["cost_gbp_at_home_rate"] = round(kwh * rate / 100, 2)
    return out

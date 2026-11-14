"""Open-Meteo: the hourly forecast (api.open-meteo.com) and past weather (archive-api.open-meteo.com), no key. Free for
non-commercial use; data CC BY 4.0, credited on the page. The reading and the sums are pure, so they are tested on
their own.

Heating degree-hours: for each hour, how far the temperature is below 15.5 °C (the UK's usual base: below it, a home
needs heating), added up. Twice the degree-hours is about twice the heat a heat pump has to find.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = ("temperature_2m", "apparent_temperature", "precipitation", "cloud_cover", "wind_speed_10m",
          "shortwave_radiation", "weather_code")  # fmt: skip
BASE_C = 15.5
CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
         66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
         80: "light showers", 81: "showers", 82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
         95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail"}  # fmt: skip


def hours(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Open-Meteo's hourly arrays as rows (times in UTC: asked for with timezone=GMT). Raises ValueError otherwise."""
    h = body.get("hourly") if isinstance(body, dict) else None
    if not isinstance(h, dict) or not isinstance(h.get("time"), list):
        raise ValueError("Open-Meteo: no hourly data in the answer")  # noqa: TRY004 - callers catch ValueError for any bad answer
    out = []
    for i, t in enumerate(h["time"]):
        def v(name: str, i: int = i) -> Any:
            col = h.get(name) or []
            return col[i] if i < len(col) else None

        out.append({"at": datetime.fromisoformat(t).replace(tzinfo=UTC), "temp_c": v("temperature_2m"),
                    "apparent_c": v("apparent_temperature"), "precip_mm": v("precipitation"), "cloud_pct": v("cloud_cover"),
                    "wind_kph": v("wind_speed_10m"), "radiation_wm2": v("shortwave_radiation"),
                    "code": int(v("weather_code")) if v("weather_code") is not None else None})  # fmt: skip
    return out


def degree_hours(rows: list[dict[str, Any]], base: float = BASE_C) -> float:
    return round(sum(max(0.0, base - r["temp_c"]) for r in rows if r.get("temp_c") is not None), 1)


def window(rows: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    return [r for r in rows if start <= r["at"] < end]


def summary(rows: list[dict[str, Any]], day: date, tz: Any) -> dict[str, Any]:
    """A day (local) in brief: low, high, rain, sunshine, heating degree-hours all day and 16:00 to 19:00."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    day_rows = window(rows, start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC))
    eve = window(rows, (start + timedelta(hours=16)).astimezone(UTC), (start + timedelta(hours=19)).astimezone(UTC))
    temps = [r["temp_c"] for r in day_rows if r["temp_c"] is not None]
    codes = [r["code"] for r in day_rows if r["code"] is not None]
    return {
        "day": day.isoformat(), "hours": len(day_rows),
        "low_c": min(temps) if temps else None, "high_c": max(temps) if temps else None,
        "rain_mm": round(sum(r["precip_mm"] or 0 for r in day_rows), 1),
        "sun_kwh_m2": round(sum(r["radiation_wm2"] or 0 for r in day_rows) / 1000, 2),
        "degree_hours": degree_hours(day_rows),
        "evening_c": round(sum(r["temp_c"] for r in eve if r["temp_c"] is not None) / len(eve), 1) if eve else None,
        "evening_degree_hours": degree_hours(eve),
        "mostly": CODES.get(max(set(codes), key=codes.count)) if codes else None,
    }  # fmt: skip


async def forecast(client: httpx.AsyncClient, lat: float, lon: float, days: int = 7, past_days: int = 2) -> list[dict[str, Any]]:
    r = await client.get(FORECAST, params={"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(HOURLY),
                                           "timezone": "GMT", "forecast_days": days, "past_days": past_days, "wind_speed_unit": "kmh"},
                         timeout=20)  # fmt: skip
    r.raise_for_status()
    return hours(r.json())


async def archive(client: httpx.AsyncClient, lat: float, lon: float, start: date, end: date) -> list[dict[str, Any]]:
    r = await client.get(ARCHIVE, params={"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(HOURLY),
                                          "timezone": "GMT", "start_date": start.isoformat(), "end_date": end.isoformat(),
                                          "wind_speed_unit": "kmh"}, timeout=40)  # fmt: skip
    r.raise_for_status()
    return hours(r.json())

"""Air quality (Defra UK-AIR) and river levels, rainfall and tide gauges (Environment Agency), read live.

UK-AIR: the Sensor Observation Service's REST API (52°North Timeseries API v1), Open Government Licence, no key.
    GET {UKAIR}/stations                                     every station (kept a day); the nearest are found here
                                                             (its own ?near= search answered HTTP 500 in Nov 2026)
    GET {UKAIR}/timeseries?station={id}&expanded=true        each series' pollutant, unit and last value
    GET {UKAIR}/timeseries/{id}/getData?timespan=PT24H/<now>  the readings, for the 8- and 24-hour means
The Daily Air Quality Index (1 to 10) is worked out here from those readings, as Defra defines it.

Environment Agency flood-monitoring API (the one flood warnings come from), Open Government Licence, no key.
    GET {EA}/id/stations?parameter=level|rainfall&lat=&long=&dist=km, or type=Coastal for tide gauges
    GET {EA}/id/stations/{ref}                  measures with their latest reading, and the river's usual range
    GET {EA}/id/measures/{id}/readings?since=   recent readings, for a trend or a rain total

Both answer in seconds and change every 15 minutes to an hour, so answers are kept for ten minutes.
"""

from __future__ import annotations

import math
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

UKAIR = "https://uk-air.defra.gov.uk/sos-ukair/api/v1"
EA = "https://environment.data.gov.uk/flood-monitoring"
USER_AGENT = "world-assistant (self-hosted)"
CACHE_S = 600
UKAIR_ATTRIBUTION = "Defra UK-AIR, Open Government Licence v3.0"
EA_ATTRIBUTION = "Environment Agency flood and river level data, Open Government Licence v3.0"


class EnvError(Exception):
    """A source could not be read or answered something unexpected."""


_cache: dict[str, tuple[float, Any]] = {}


def forget() -> None:
    _cache.clear()
    _ukair_stations.clear()


async def _json(client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None) -> Any:
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        r = await client.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise EnvError(f"{_host(url)} did not answer ({type(exc).__name__})") from exc
    if r.status_code >= 400:
        raise EnvError(f"{_host(url)} answered HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError as exc:
        raise EnvError(f"{_host(url)} did not send JSON") from exc
    _cache[key] = (time.monotonic(), data)
    return data


def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _num(v: Any) -> float | None:
    if isinstance(v, list):  # the EA sometimes sends two values for one reading
        v = v[0] if v else None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _when(v: Any) -> datetime | None:
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, UTC)
    if isinstance(v, str) and v:
        try:
            t = datetime.fromisoformat(v)
        except ValueError:
            return None
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    return None


# --- air quality ---------------------------------------------------------------------------------------------------

POLLUTANTS = {"no2": "Nitrogen dioxide", "o3": "Ozone", "pm25": "PM2.5", "pm10": "PM10"}
# Defra's Daily Air Quality Index: the top of each band 1 to 9 in µg/m³ (10 is anything above band 9). NO2 is the
# hourly mean, ozone the running 8-hour mean, the particles the running 24-hour mean.
DAQI_BANDS = {
    "o3": (33, 66, 100, 120, 140, 160, 187, 213, 240),
    "no2": (67, 134, 200, 267, 334, 400, 467, 534, 600),
    "pm25": (11, 23, 35, 41, 47, 53, 58, 64, 70),
    "pm10": (16, 33, 50, 58, 66, 75, 83, 91, 100),
}
MEAN_HOURS = {"no2": 1, "o3": 8, "pm25": 24, "pm10": 24}


def daqi(pollutant: str, value: float) -> int:
    """The index (1 to 10) for one pollutant's concentration, in µg/m³."""
    for i, top in enumerate(DAQI_BANDS[pollutant], start=1):
        if round(value) <= top:
            return i
    return 10


def daqi_band(index: int) -> str:
    return "Low" if index <= 3 else "Moderate" if index <= 6 else "High" if index <= 9 else "Very high"


def pollutant_of(label: str) -> str | None:
    """Which of the four a series measures, from its phenomenon label ("Nitrogen dioxide (air)", "Particulate
    matter < 2.5 µm (aerosol)", "PM10 particulate matter (Hourly measured)" and the like)."""
    s = (label or "").lower()
    if "nitrogen dioxide" in s or re.search(r"\bno2\b", s):
        return "no2"
    if "ozone" in s or re.search(r"\bo3\b", s):
        return "o3"
    if "2.5" in s or "pm25" in s:
        return "pm25"
    if re.search(r"pm\s*10\b", s) or re.search(r"<\s*10\s*µ?m", s) or "particulate matter < 10" in s:
        return "pm10"
    return None


def station_point(feature: dict[str, Any]) -> tuple[float, float] | None:
    """(lat, lon) of a station. Its GeoJSON should be [lon, lat], but some instances send [lat, lon]: in Britain
    the latitude is the one between 49 and 61."""
    c = (feature.get("geometry") or {}).get("coordinates") or []
    if len(c) < 2:
        return None
    x, y = _num(c[0]), _num(c[1])
    if x is None or y is None:
        return None
    return (x, y) if 49 <= x <= 61 and not 49 <= y <= 61 else (y, x)


def _values(data: Any, series_id: str) -> list[tuple[datetime, float]]:
    """getData's readings, in either of its shapes ({values: [...]} or {<id>: {values: [...]}})."""
    if isinstance(data, dict) and "values" not in data and series_id in data:
        data = data[series_id]
    out = []
    for v in (data or {}).get("values") or [] if isinstance(data, dict) else []:
        t, n = _when(v.get("timestamp")), _num(v.get("value"))
        if t and n is not None and n >= 0:
            out.append((t, n))
    return sorted(out)


STATIONS_S = 86400
_ukair_stations: dict[str, Any] = {}


async def all_stations(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Every UK-AIR station (a few hundred), kept for a day: stations rarely change, and UK-AIR's own nearby search
    (?near=) fails. If a fresh list cannot be had, the last one is used."""
    if _ukair_stations.get("at") and time.monotonic() - _ukair_stations["at"] < STATIONS_S:
        return _ukair_stations["list"]
    try:
        got = await _json(client, f"{UKAIR}/stations", None)
    except EnvError:
        if _ukair_stations.get("list"):
            return _ukair_stations["list"]
        raise
    if not isinstance(got, list):
        raise EnvError("UK-AIR sent no list of stations")
    _ukair_stations.update(at=time.monotonic(), list=got)
    return got


async def air_near(client: httpx.AsyncClient, lat: float, lon: float, km: float = 20,
                   now: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """Each pollutant from the nearest station measuring it recently, its mean as the index uses it, and the
    overall index (the highest of the four)."""
    now = now or datetime.now(UTC)
    stations = await all_stations(client)
    placed = []
    for f in stations:
        pt = station_point(f)
        sid = (f.get("properties") or {}).get("id")
        if pt and sid is not None and (d := km_between(lat, lon, *pt)) <= km:
            placed.append((d, str(sid), (f.get("properties") or {}).get("label") or str(sid), pt))
    placed.sort()
    readings: dict[str, dict[str, Any]] = {}
    for dist, sid, label, pt in placed[:8]:
        if len(readings) == len(POLLUTANTS):
            break
        series = await _json(client, f"{UKAIR}/timeseries", {"station": sid, "expanded": "true"})
        for ts in series if isinstance(series, list) else []:
            label_ = ((ts.get("parameters") or {}).get("phenomenon") or {}).get("label") or ts.get("label") or ""
            p = pollutant_of(label_)
            last = ts.get("lastValue") or {}
            when, value = _when(last.get("timestamp")), _num(last.get("value"))
            if not p or p in readings or when is None or value is None or now - when > timedelta(hours=6):
                continue
            mean = value
            if MEAN_HOURS[p] > 1:
                span = f"PT{MEAN_HOURS[p]}H/{now.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                vals = _values(await _json(client, f"{UKAIR}/timeseries/{ts['id']}/getData", {"timespan": span}), str(ts["id"]))
                if len(vals) >= max(1, MEAN_HOURS[p] * 3 // 4):  # Defra wants 75% of the hours for a mean
                    mean = sum(v for _, v in vals) / len(vals)
            readings[p] = {
                "pollutant": POLLUTANTS[p], "value": round(value, 1), "mean": round(mean, 1), "mean_hours": MEAN_HOURS[p],
                "unit": ts.get("uom") or "µg/m³", "index": daqi(p, mean), "at": when.isoformat(),
                "station": label, "station_km": round(dist, 1), "station_lat": pt[0], "station_lon": pt[1],
            }  # fmt: skip
    if not readings:
        return {"index": None, "band": None, "readings": {}, "source": UKAIR_ATTRIBUTION,
                "note": f"no station within {km:g} km has a reading from the last six hours"}  # fmt: skip
    top = max(readings.values(), key=lambda r: r["index"])
    return {"index": top["index"], "band": daqi_band(top["index"]), "worst": top["pollutant"], "readings": readings,
            "source": UKAIR_ATTRIBUTION}  # fmt: skip


# --- rivers, rain and tides ---------------------------------------------------------------------------------------


def _https(url: str) -> str:
    return url.replace("http://", "https://", 1)


async def _stations(client: httpx.AsyncClient, lat: float, lon: float, km: float, **filters: str) -> list[dict[str, Any]]:
    data = await _json(client, f"{EA}/id/stations", {"lat": lat, "long": lon, "dist": km, "_limit": 50, **filters})
    out = []
    for s in (data or {}).get("items") or []:
        slat, slon = _num(s.get("lat")), _num(s.get("long"))
        if slat is None or slon is None or not s.get("stationReference"):
            continue
        out.append({**s, "_km": km_between(lat, lon, slat, slon)})
    return sorted(out, key=lambda s: s["_km"])


async def _detail(client: httpx.AsyncClient, ref: str) -> dict[str, Any]:
    d = await _json(client, f"{EA}/id/stations/{ref}")
    items = (d or {}).get("items")
    return (items[0] if isinstance(items, list) and items else items) or {}


def _measures(station: dict[str, Any]) -> list[dict[str, Any]]:
    m = station.get("measures")
    return m if isinstance(m, list) else [m] if isinstance(m, dict) else []


async def _recent(client: httpx.AsyncClient, measure: str, since: datetime) -> list[tuple[datetime, float]]:
    data = await _json(client, _https(measure) + "/readings", {"since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "_sorted": "", "_limit": 500})
    out = []
    for r in (data or {}).get("items") or []:
        t, v = _when(r.get("dateTime")), _num(r.get("value"))
        if t and v is not None:
            out.append((t, v))
    return sorted(out)


def trend(readings: list[tuple[datetime, float]], step: float = 0.02) -> str | None:
    """rising, falling or steady over the readings given (a change under `step` is steady)."""
    if len(readings) < 2:
        return None
    d = readings[-1][1] - readings[0][1]
    return "rising" if d > step else "falling" if d < -step else "steady"


async def rivers_near(client: httpx.AsyncClient, lat: float, lon: float, km: float = 5, limit: int = 3,
                      now: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """The nearest river level gauges: the level now, the river's usual range there, and the trend over 3 hours."""
    now = now or datetime.now(UTC)
    out = []
    for s in [s for s in await _stations(client, lat, lon, km, parameter="level") if s.get("type") != "Coastal"
              and "Coastal" not in str(s.get("type"))][:limit]:  # fmt: skip
        d = await _detail(client, s["stationReference"])
        m = next((m for m in _measures(d) if m.get("parameter") == "level" and "stage" in str(m.get("qualifier", "")).lower()),
                 next((m for m in _measures(d) if m.get("parameter") == "level"), None))  # fmt: skip
        if not m:
            continue
        latest = m.get("latestReading") if isinstance(m.get("latestReading"), dict) else {}
        value, when = _num(latest.get("value")), _when(latest.get("dateTime"))
        scale = d.get("stageScale") if isinstance(d.get("stageScale"), dict) else {}
        low, high = _num(scale.get("typicalRangeLow")), _num(scale.get("typicalRangeHigh"))
        state = None
        if value is not None and low is not None and high is not None:
            state = "low" if value < low else "high" if value > high else "normal"
        rec = await _recent(client, m["@id"], now - timedelta(hours=3)) if m.get("@id") else []
        out.append({
            "station": d.get("label") or s.get("label"), "river": d.get("riverName") or s.get("riverName"),
            "town": d.get("town") or s.get("town"), "km": round(s["_km"], 1), "level_m": value,
            "at": when.isoformat() if when else None, "typical_low_m": low, "typical_high_m": high, "state": state,
            "trend": trend(rec), "unit": m.get("unitName") or "m", "ref": s["stationReference"],
        })  # fmt: skip
    return {"rivers": out, "count": len(out), "source": EA_ATTRIBUTION}


async def rain_near(client: httpx.AsyncClient, lat: float, lon: float, km: float = 10, limit: int = 2,
                    now: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """The nearest rain gauges: millimetres in the last hour and the last 24 hours."""
    now = now or datetime.now(UTC)
    out = []
    for s in (await _stations(client, lat, lon, km, parameter="rainfall"))[:limit]:
        m = next((m for m in _measures(s) if "rainfall" in str(m.get("parameter", "")) or "rainfall" in str(m.get("@id", ""))), None)
        if not m or not m.get("@id"):
            m = next((m for m in _measures(await _detail(client, s["stationReference"])) if m.get("parameter") == "rainfall"), None)
        if not m or not m.get("@id"):
            continue
        rec = await _recent(client, m["@id"], now - timedelta(hours=24))
        # each reading is the 15 minutes up to its time, so the one exactly an hour ago belongs to the hour before
        hour = [v for t, v in rec if now - t < timedelta(hours=1)]
        out.append({"gauge": s.get("label") or s["stationReference"], "km": round(s["_km"], 1),
                    "last_hour_mm": round(sum(hour), 1), "last_24h_mm": round(sum(v for _, v in rec), 1),
                    "at": rec[-1][0].isoformat() if rec else None, "ref": s["stationReference"]})  # fmt: skip
    return {"gauges": out, "count": len(out), "source": EA_ATTRIBUTION}


async def tides_near(client: httpx.AsyncClient, lat: float, lon: float, km: float = 20,
                     now: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """The nearest tide gauge: the measured level now and whether it is rising or falling (over the last hour).
    Measured levels, not predictions: high and low water times are not in this data."""
    now = now or datetime.now(UTC)
    for s in await _stations(client, lat, lon, km, type="Coastal"):
        d = await _detail(client, s["stationReference"])
        m = next((m for m in _measures(d) if m.get("parameter") == "level"), None)
        if not m:
            continue
        latest = m.get("latestReading") if isinstance(m.get("latestReading"), dict) else {}
        value, when = _num(latest.get("value")), _when(latest.get("dateTime"))
        if value is None:
            continue
        rec = await _recent(client, m["@id"], now - timedelta(hours=1)) if m.get("@id") else []
        return {"gauge": d.get("label") or s.get("label"), "km": round(s["_km"], 1), "level_m": value,
                "unit": m.get("unitName") or "mAOD", "at": when.isoformat() if when else None,
                "trend": trend(rec, step=0.05), "ref": s["stationReference"], "source": EA_ATTRIBUTION}  # fmt: skip
    return {"gauge": None, "source": EA_ATTRIBUTION, "note": f"no tide gauge within {km:g} km"}


# --- Storm overflows: Water UK's National Storm Overflow Hub (each company's "Storm Overflow Activity" layer on
# Stream, CC BY 4.0). Fields follow the Stream standard: Id, Company, Status (1 discharging, 0 not, -1 offline),
# StatusStart, LatestEventStart, LatestEventEnd, Latitude, Longitude, ReceivingWaterCourse, LastUpdated.
OVERFLOW_FEEDS = {
    "Thames Water": "https://services2.arcgis.com/g6o32ZDQ33GpCIu3/arcgis/rest/services/"
                    "Thames_Water_Storm_Overflow_Activity_(Production)_view/FeatureServer/0/query",
}  # fmt: skip
OVERFLOW_ATTRIBUTION = "Water UK National Storm Overflow Hub (Stream), CC BY 4.0"
OVERFLOW_STATUS = {1: "discharging", 0: "not discharging", -1: "offline"}
RECENT_H = 48


def _field(attrs: dict[str, Any], *names: str) -> Any:
    """A field by any of its names, ignoring case (layers differ in capitals and add their own fields)."""
    low = {k.lower(): v for k, v in attrs.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, ""):
            return v
    return None


def overflow(attrs: dict[str, Any], lat: float, lon: float, now: datetime) -> dict[str, Any] | None:
    """One overflow as read here, or None without a location."""
    olat, olon = _num(_field(attrs, "Latitude", "lat")), _num(_field(attrs, "Longitude", "lon", "long"))
    if olat is None or olon is None:
        return None
    raw = _field(attrs, "Status", "AlertStatus")
    try:
        status = OVERFLOW_STATUS.get(int(raw), "unknown")
    except (TypeError, ValueError):
        status = {"discharging": "discharging", "not discharging": "not discharging", "offline": "offline"}.get(
            str(raw).strip().lower(), "unknown")  # fmt: skip
    last_end = _when(_field(attrs, "LatestEventEnd"))
    since = _when(_field(attrs, "StatusStart"))
    return {
        "id": _field(attrs, "Id", "PermitNumber"), "name": _field(attrs, "LocationName", "SiteName", "Name", "Id"),
        "river": _field(attrs, "ReceivingWaterCourse", "ReceivingWater"), "status": status,
        "since": since, "last_start": _when(_field(attrs, "LatestEventStart")), "last_end": last_end,
        "recent": status == "discharging" or bool(last_end and now - last_end < timedelta(hours=RECENT_H)),
        "hours": round((now - since).total_seconds() / 3600, 1) if status == "discharging" and since else None,
        "updated": _when(_field(attrs, "LastUpdated")), "company": _field(attrs, "Company"),
        "km": round(km_between(lat, lon, olat, olon), 2), "lat": olat, "lon": olon,
    }  # fmt: skip


def _watching(river: Any, rivers: list[str]) -> bool:
    name = str(river or "").lower()
    return any(w.strip().lower() in name for w in rivers if w.strip())


async def overflows_near(client: httpx.AsyncClient, lat: float, lon: float, km: float = 3,
                         rivers: list[str] | None = None, river_km: float = 15,
                         now: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """Storm overflows within `km`, and those on the watched rivers within `river_km`: which are discharging now,
    which have in the last 48 hours, and which monitors are offline."""
    now = now or datetime.now(UTC)
    rivers = [r for r in (rivers or []) if r.strip()]
    reach = max(km, river_km if rivers else 0)
    found: list[dict[str, Any]] = []
    errors: list[str] = []
    for company, url in OVERFLOW_FEEDS.items():
        offset = 0
        for _ in range(5):  # at most 5 pages
            params = {"where": "1=1", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
                      "spatialRel": "esriSpatialRelIntersects", "distance": str(int(reach * 1000)),
                      "units": "esriSRUnit_Meter", "outFields": "*", "returnGeometry": "false", "f": "json",
                      "resultOffset": str(offset), "resultRecordCount": "1000"}  # fmt: skip
            try:
                data = await _json(client, url, params)
            except EnvError as exc:
                errors.append(f"{company}: {exc}")
                break
            if not isinstance(data, dict) or "error" in data:
                errors.append(f"{company}: {(data or {}).get('error', {}).get('message', 'unexpected answer') if isinstance(data, dict) else 'unexpected answer'}")
                break
            feats = data.get("features") or []
            for f in feats:
                o = overflow(f.get("attributes") or {}, lat, lon, now)
                if o and (o["km"] <= km or (_watching(o["river"], rivers) and o["km"] <= river_km)):
                    o["watched"] = _watching(o["river"], rivers)
                    found.append(o)
            if not data.get("exceededTransferLimit") or not feats:
                break
            offset += len(feats)
    if errors and not found:
        raise EnvError("; ".join(errors))
    found.sort(key=lambda o: (o["status"] != "discharging", not o["recent"], o["km"]))
    return {
        "km": km, "rivers": rivers, "river_km": river_km if rivers else None, "count": len(found),
        "discharging": [o for o in found if o["status"] == "discharging"],
        "recent": [o for o in found if o["recent"] and o["status"] != "discharging"],
        "offline": sum(o["status"] == "offline" for o in found),
        "overflows": found[:50], "errors": errors or None,
        "source": OVERFLOW_ATTRIBUTION,
        "note": "Indicative: a monitor reports that the overflow is spilling, not how much or how dirty.",
    }  # fmt: skip

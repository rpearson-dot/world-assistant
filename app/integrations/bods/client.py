"""Live bus positions from the Bus Open Data Service (BODS), for England, Scotland and Wales.

BODS publishes the position of every bus that reports one, as SIRI-VM XML:

    GET https://data.bus-data.dft.gov.uk/api/v1/datafeed/?boundingBox=minLon,minLat,maxLon,maxLat&api_key=KEY

Only the buses inside a box are asked for. The key is free (register at data.bus-data.dft.gov.uk) and
lives in the `bods_api_key` secret. Positions are not stored: they are a few seconds old and worth
nothing a minute later, so they are cached briefly and drawn.

Not run against the live service from where this was written; the parser is tested on a response built
to the SIRI-VM shape, and ignores namespaces and any field it does not know.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # a DOCTYPE is refused before parsing, see parse_vehicles
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

BASE_URL = "https://data.bus-data.dft.gov.uk/api/v1/datafeed/"
MAX_BYTES = 15 * 1024 * 1024  # a city's worth of buses is well under this; more means something is wrong
UK_LAT, UK_LON = (49.5, 61.0), (-8.7, 2.0)


class BodsError(Exception):
    """The feed could not be read, or no key is set."""


@dataclass(frozen=True)
class Box:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def param(self) -> str:
        """In the order BODS wants: minLongitude, minLatitude, maxLongitude, maxLatitude."""
        return f"{self.min_lon:.4f},{self.min_lat:.4f},{self.max_lon:.4f},{self.max_lat:.4f}"


def box_around(lat: float, lon: float, radius_km: float) -> Box:
    """A box that contains the circle. A degree of latitude is 111 km; longitude shrinks with latitude."""
    import math

    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.1))
    return Box(lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element) -> dict[str, ET.Element]:
    return {_local(c.tag): c for c in element}


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_vehicles(body: bytes) -> list[dict[str, Any]]:
    """Vehicles from a SIRI-VM document. Anything without a usable position is dropped."""
    if len(body) > MAX_BYTES:
        raise BodsError("the bus feed was larger than expected")
    lowered = body.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise BodsError("the bus feed carried a DOCTYPE, which it should not")  # no entity tricks
    try:
        root = ET.fromstring(body)  # DOCTYPE and entities were refused above
    except ET.ParseError as exc:
        raise BodsError(f"the bus feed was not valid XML: {exc}") from exc
    out: list[dict[str, Any]] = []
    for activity in root.iter():
        if _local(activity.tag) != "VehicleActivity":
            continue
        parts = _children(activity)
        journey = _children(parts["MonitoredVehicleJourney"]) if "MonitoredVehicleJourney" in parts else {}
        location = _children(journey["VehicleLocation"]) if "VehicleLocation" in journey else {}
        try:
            lat, lon = float(_text(location.get("Latitude")) or ""), float(_text(location.get("Longitude")) or "")
        except ValueError:
            continue
        if not (UK_LAT[0] <= lat <= UK_LAT[1] and UK_LON[0] <= lon <= UK_LON[1]):
            continue
        try:
            bearing = float(_text(journey.get("Bearing")) or "")
        except ValueError:
            bearing = None
        recorded = _time(_text(parts.get("RecordedAtTime")))
        out.append({
            "vehicle": _text(journey.get("VehicleRef")),
            "line": _text(journey.get("PublishedLineName")) or _text(journey.get("LineRef")),
            "operator": _text(journey.get("OperatorRef")),
            "destination": _text(journey.get("DestinationName")),
            "direction": _text(journey.get("DirectionRef")),
            "bearing": bearing, "lat": lat, "lon": lon,
            "recorded_at": recorded.isoformat() if recorded else None,
        })  # fmt: skip
    return out


async def fetch_vehicles(api_key: str, box: Box, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    if not api_key:
        raise BodsError("the bods_api_key secret is not set")
    owned = client is None
    client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)
    try:
        try:
            response = await client.get(BASE_URL, params={"boundingBox": box.param(), "api_key": api_key})
        except httpx.HTTPError as exc:
            raise BodsError(f"the bus feed could not be reached: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise BodsError("BODS refused the key (check the bods_api_key secret)")
        if response.status_code != 200:
            raise BodsError(f"the bus feed answered HTTP {response.status_code}")
        return parse_vehicles(response.content)
    finally:
        if owned:
            await client.aclose()


DATASET_URL = "https://data.bus-data.dft.gov.uk/api/v1/dataset/"
MAX_DOWNLOAD = 80 * 1024 * 1024
MAX_DATASETS = 12  # timetable files tried while looking for a line


async def operators_of_line(api_key: str, line: str, box: Box, client: httpx.AsyncClient | None = None) -> list[str]:
    """Operator codes (NOC) of the buses reporting this line inside the box, most buses first."""
    vehicles = await fetch_vehicles(api_key, box, client)
    counts: dict[str, int] = {}
    for v in vehicles:
        if (v.get("line") or "").upper() == line.upper() and v.get("operator"):
            counts[v["operator"]] = counts.get(v["operator"], 0) + 1
    return sorted(counts, key=lambda n: -counts[n])


async def find_datasets(
    api_key: str, *, noc: str | None = None, search: str | None = None, limit: int = MAX_DATASETS,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Published timetable datasets, [{id, name, url, operator}], for an operator or a search word."""
    if not api_key:
        raise BodsError("the bods_api_key secret is not set")
    params: dict[str, Any] = {"api_key": api_key, "status": "published", "limit": limit}
    if noc:
        params["noc"] = noc
    if search:
        params["search"] = search
    owned = client is None
    client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        try:
            r = await client.get(DATASET_URL, params=params)
        except httpx.HTTPError as exc:
            raise BodsError(f"the timetable list could not be reached: {type(exc).__name__}") from exc
        if r.status_code in (401, 403):
            raise BodsError("BODS refused the key (check the bods_api_key secret)")
        if r.status_code != 200:
            raise BodsError(f"the timetable list answered HTTP {r.status_code}")
        try:
            rows = r.json().get("results") or []
        except (ValueError, AttributeError) as exc:
            raise BodsError("the timetable list was not JSON") from exc
    finally:
        if owned:
            await client.aclose()
    return [{"id": d.get("id"), "name": d.get("name") or "", "url": d.get("url"),
             "operator": d.get("operatorName") or ""}
            for d in rows if isinstance(d, dict) and isinstance(d.get("url"), str)]  # fmt: skip


async def download_txc(url: str, client: httpx.AsyncClient | None = None) -> list[bytes]:
    """The XML documents at a timetable URL: one file, or every .xml inside a zip."""
    import io
    import zipfile

    if not url.startswith("https://data.bus-data.dft.gov.uk/"):
        raise BodsError("refusing to download a timetable from outside BODS")
    owned = client is None
    client = client or httpx.AsyncClient(timeout=120.0, follow_redirects=True)
    try:
        try:
            r = await client.get(url)
        except httpx.HTTPError as exc:
            raise BodsError(f"the timetable could not be downloaded: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise BodsError(f"the timetable download answered HTTP {r.status_code}")
        body = r.content
    finally:
        if owned:
            await client.aclose()
    if len(body) > MAX_DOWNLOAD:
        raise BodsError("the timetable download was larger than expected")
    if body[:2] != b"PK":
        return [body]
    out, total = [], 0
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        for info in z.infolist():
            if info.filename.lower().endswith(".xml"):
                total += info.file_size
                if total > MAX_DOWNLOAD:
                    raise BodsError("the timetable archive unpacks larger than expected")
                out.append(z.read(info))
    return out

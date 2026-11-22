"""Location lookups, all keyless and UK-only:

- postcode -> position: the local Code-Point Open table when a module provides it ("postcodes.local"), else
  postcodes.io;
- a position -> its nearest postcode (postcodes.io), to label a place saved from the phone's location;
- free text ("the O2 in London") -> a few candidate places (Nominatim / OpenStreetMap), so a described place can be
  found and the right one picked. The Nominatim base URL is configurable so it can point at a self-hosted instance
  rather than the shared public one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

POSTCODES_URL = "https://api.postcodes.io"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
# Nominatim's usage policy asks for an identifying User-Agent; a self-hosted instance ignores it.
GEOCODER_UA = "WorldAssistant/1.0 (self-hosted personal assistant; place search)"
_POSTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$")

# Great Britain and its waters, matching app.places.PlaceIn's bounds.
_LAT_MIN, _LAT_MAX = 49.5, 61.0
_LON_MIN, _LON_MAX = -8.7, 2.0


class PostcodeError(Exception):
    """The postcode was malformed, unknown, or the lookup service failed."""


class GeocodeError(Exception):
    """The place search service was unreachable or gave something unexpected."""


@dataclass(frozen=True)
class Located:
    postcode: str
    lat: float
    lon: float


@dataclass(frozen=True)
class PlaceHit:
    """One candidate for a described place: a short label, a position, its postcode if known, and a rough kind."""

    label: str
    lat: float
    lon: float
    postcode: str | None = None
    kind: str | None = None


def _in_uk(lat: float, lon: float) -> bool:
    return _LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lon <= _LON_MAX


def normalise_postcode(value: str) -> str:
    """Upper-case with the single space before the last three characters."""
    compact = re.sub(r"\s+", "", value).upper()
    if not _POSTCODE.match(compact):
        raise PostcodeError(f"{value!r} does not look like a UK postcode")
    return f"{compact[:-3]} {compact[-3:]}"


async def lookup_postcode(
    postcode: str,
    *,
    base_url: str = POSTCODES_URL,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Located:
    pc = normalise_postcode(postcode)
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=10.0, transport=transport, follow_redirects=False
        ) as client:
            resp = await client.get(f"/postcodes/{pc.replace(' ', '')}")
    except httpx.TransportError as exc:
        raise PostcodeError(f"postcode lookup failed: {type(exc).__name__}") from exc
    if resp.status_code == 404:
        raise PostcodeError(f"{pc} was not found")
    if resp.status_code != 200:
        raise PostcodeError(f"postcode lookup returned HTTP {resp.status_code}")
    try:
        result = resp.json()["result"]
        lat, lon = result["latitude"], result["longitude"]
    except (ValueError, KeyError, TypeError) as exc:
        raise PostcodeError("unexpected response from postcode lookup") from exc
    if lat is None or lon is None:
        raise PostcodeError(f"{pc} has no coordinates")
    return Located(pc, float(lat), float(lon))


async def local_postcode(engine: object, value: str) -> Located | None:
    """A postcode's position from the module that keeps the local table (Code-Point Open), or None: no such module,
    or it does not know the postcode. None means "ask postcodes.io"."""
    from app.modules import registry

    find = registry.provider("postcodes.local")
    return await find(engine, value) if find else None


async def reverse_postcode(
    lat: float,
    lon: float,
    *,
    base_url: str = POSTCODES_URL,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """The nearest postcode to a position (postcodes.io), to label a place saved from the phone's location. Returns
    None rather than raising: a missing label must never stop a place being saved."""
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=10.0, transport=transport, follow_redirects=False
        ) as client:
            resp = await client.get("/postcodes", params={"lat": lat, "lon": lon, "limit": 1})
        if resp.status_code != 200:
            return None
        result = resp.json().get("result") or []
        return result[0]["postcode"] if result else None
    except (httpx.TransportError, ValueError, KeyError, TypeError, IndexError):
        return None


def _hit_label(name: str, address: dict[str, object]) -> str:
    """A short label: the place's own name, then the most telling locality, so two candidates read differently."""
    locality = next(
        (str(address[k]) for k in ("suburb", "village", "town", "city", "county") if address.get(k)),
        None,
    )
    return f"{name}, {locality}" if locality and locality != name else name


async def search_places(
    query: str,
    *,
    near: tuple[float, float] | None = None,
    limit: int = 6,
    base_url: str = NOMINATIM_URL,
    user_agent: str = GEOCODER_UA,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[PlaceHit]:
    """Candidate places for a free-text query, UK only (Nominatim / OpenStreetMap). When `near` is given, results in
    that area are preferred (a viewbox hint, not a hard limit) so "the O2" near the phone ranks the closest first."""
    q = query.strip()
    if not q:
        return []
    params: dict[str, str | int] = {
        "q": q,
        "format": "jsonv2",
        "countrycodes": "gb",
        "addressdetails": 1,
        "limit": max(1, min(limit, 10)),
    }
    if near is not None:
        lat, lon = near
        d = 0.6  # ~45-65 km box; preferred, not bounded
        params["viewbox"] = f"{lon - d},{lat + d},{lon + d},{lat - d}"
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=12.0, transport=transport, follow_redirects=False,
            headers={"User-Agent": user_agent, "Accept-Language": "en-GB"},
        ) as client:  # fmt: skip
            resp = await client.get("/search", params=params)
    except httpx.TransportError as exc:
        raise GeocodeError(f"place search failed: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise GeocodeError(f"place search returned HTTP {resp.status_code}")
    try:
        rows = resp.json()
    except ValueError as exc:
        raise GeocodeError("unexpected response from place search") from exc
    hits: list[PlaceHit] = []
    for row in rows if isinstance(rows, list) else []:
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, ValueError, TypeError):
            continue
        if not _in_uk(lat, lon):
            continue
        address = row.get("address") or {}
        name = str(row.get("name") or "").strip() or str(row.get("display_name") or "").split(",")[0].strip()
        if not name:
            continue
        hits.append(PlaceHit(
            label=_hit_label(name, address),
            lat=lat,
            lon=lon,
            postcode=(str(address["postcode"]) if address.get("postcode") else None),
            kind=(str(row["type"]) if row.get("type") else None),
        ))  # fmt: skip
    return hits


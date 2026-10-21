"""Postcode lookup using postcodes.io (free, no key, UK only)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

POSTCODES_URL = "https://api.postcodes.io"
_POSTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$")


class PostcodeError(Exception):
    """The postcode was malformed, unknown, or the lookup service failed."""


@dataclass(frozen=True)
class Located:
    postcode: str
    lat: float
    lon: float


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

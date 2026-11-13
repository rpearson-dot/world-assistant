"""NaPTAN rows, as the national CSV gives them, into what we store.

Pure: a dict in, a dict or None out, so the rules can be tested without a database or a download.

NaPTAN describes one real place as several records (a station has an access area, entrances and a
platform each), so `StopType` is mapped to a coarser `kind` for display. The types are the ones the
NaPTAN schema defines; anything else (taxi ranks, car parks) is not a place a bus or train calls.
"""

from __future__ import annotations

from typing import Any

# StopType -> kind
KINDS: dict[str, str] = {
    "BCT": "bus_stop",  # on-street bus, coach or tram stop
    "BCS": "bus_stop",  # bay in a bus station
    "BCQ": "bus_stop",  # variable bay in a bus station
    "BST": "bus_station",  # access area
    "BCE": "bus_station",  # entrance
    "RLY": "rail",  # access area
    "RSE": "rail",  # entrance
    "RPL": "rail",  # platform
    "TMU": "metro",  # access area
    "MET": "metro",  # entrance
    "PLT": "metro",  # platform
    "FTD": "ferry",  # access area
    "FER": "ferry",  # entrance
    "FBT": "ferry",  # berth
    "GAT": "air",  # access area
    "AIR": "air",  # entrance
}

# One record per station rather than one per door and platform, for a map of the whole country.
STATION_TYPES: tuple[str, ...] = ("BST", "RLY", "TMU", "FTD", "GAT")

# The UK, with room for the islands. A row outside it is a typo, and a typo is worse than a gap.
LAT_RANGE = (49.5, 61.0)
LON_RANGE = (-8.7, 2.0)

ACTIVE = {"", "active", "act"}


def _text(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _number(row: dict[str, Any], key: str) -> float | None:
    value = _text(row, key)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """One CSV row as a stop, or None when it should not be stored."""
    atco = _text(row, "ATCOCode")
    name = _text(row, "CommonName")
    stop_type = (_text(row, "StopType") or "").upper()
    kind = KINDS.get(stop_type)
    if not atco or not name or kind is None:
        return None
    if (_text(row, "Status") or "").lower() not in ACTIVE:
        return None
    lat, lon = _number(row, "Latitude"), _number(row, "Longitude")
    if lat is None or lon is None:
        return None
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        return None
    return {
        "atco": atco,
        "naptan_code": _text(row, "NaptanCode"),
        "name": name,
        "street": _text(row, "Street"),
        "indicator": _text(row, "Indicator"),
        "locality": _text(row, "LocalityName"),
        "stop_type": stop_type,
        "kind": kind,
        "lat": lat,
        "lon": lon,
    }

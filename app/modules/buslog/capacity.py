"""How full a bus is, when BODS says. Pure.

BODS's SIRI-VM can carry occupancy: `Occupancy` in the journey (seatsAvailable, standingAvailable, full) and, in its
extensions, counts (SeatedOccupancy of SeatedCapacity, WheelchairsOccupancy of WheelchairCapacity, and
OccupancyThresholds). Operators turn it on bus by bus, and the 213 does not send it yet. Every field BODS sends is
recorded, so from the day it appears it is read here, and a weekly check (buslog_capacity_check) says when it first
appears.
"""

from __future__ import annotations

import re
from typing import Any

LEVELS = {"seatsavailable": "seats free", "standingavailable": "standing room only", "full": "full",
          "manyseatsavailable": "many seats free", "fewseatsavailable": "few seats free",
          "standingroomonly": "standing room only", "crushedstandingroomonly": "very crowded", "notacceptingpassengers": "full"}  # fmt: skip
FIELD = re.compile(r"(occupancy|capacity)", re.IGNORECASE)


def _num(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _last(fields: dict[str, Any], name: str) -> Any:
    """A field by the end of its path (BODS nests extensions differently by operator)."""
    for k, v in fields.items():
        if k.split(".")[-1].lower() == name.lower():
            return v
    return None


def read(occupancy: str | None, fields: dict[str, Any] | None) -> dict[str, Any] | None:
    """{level, words, seated, seats, wheelchairs, wheelchair_spaces} when BODS said anything about how full; else None."""
    f = fields or {}
    level = occupancy or _last(f, "Occupancy")
    seated, seats = _num(_last(f, "SeatedOccupancy")), _num(_last(f, "SeatedCapacity"))
    wc, wcs = _num(_last(f, "WheelchairsOccupancy")), _num(_last(f, "WheelchairCapacity"))
    if not level and seated is None and seats is None and wc is None:
        return None
    words = LEVELS.get(str(level or "").replace("_", "").replace(" ", "").lower(), str(level) if level else "")
    if seated is not None and seats:
        words = (words + ", " if words else "") + f"{seated} of {seats} seats taken"
    return {"level": level, "words": words, "seated": seated, "seats": seats, "wheelchairs": wc, "wheelchair_spaces": wcs}


def keys_seen(fields: dict[str, Any] | None) -> list[str]:
    """The fields that look like occupancy or capacity (for the weekly check's report)."""
    return sorted(k for k in (fields or {}) if FIELD.search(k.split(".")[-1]))

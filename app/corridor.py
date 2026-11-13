"""A corridor: a route's centreline (lat/lon waypoints) plus a buffer in metres, and the roads it expects.

Trips are routed into corridors (app/routing.py); modules match their events to them (National Highways road events
and signs). Saving one, and re-matching events to it, is the job of whichever module keeps them (provides
"corridors.save" and "corridors.rematch").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]
EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class Corridor:
    id: str
    name: str
    centreline: tuple[Point, ...]
    buffer_m: float = 500.0
    roads: frozenset[str] = field(default_factory=frozenset)  # empty: any road

    def bbox(self, margin_m: float = 0.0) -> tuple[float, float, float, float]:
        """(min_lat, min_lon, max_lat, max_lon), grown by buffer plus margin."""
        lats = [p[0] for p in self.centreline]
        lons = [p[1] for p in self.centreline]
        pad = self.buffer_m + margin_m
        dlat = math.degrees(pad / EARTH_RADIUS_M)
        dlon = dlat / math.cos(math.radians(sum(lats) / len(lats)))
        return (min(lats) - dlat, min(lons) - dlon, max(lats) + dlat, max(lons) + dlon)

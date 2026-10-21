"""Match National Highways events and signs to corridors by geometry.

A corridor is a centreline (a list of lat/lon waypoints) plus a buffer in
metres. Matching is by distance first; road name and direction are used as a
cross-check when a corridor lists the roads it expects. No database needed:
distances use a local flat-earth approximation, accurate to well under a metre
at UK scale for the distances involved.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.integrations.nationalhighways.normalise import Point, RoadEvent, Segment, VmsSign

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


def _to_xy(p: Point, lat0: float) -> tuple[float, float]:
    lat, lon = p
    x = math.radians(lon) * math.cos(math.radians(lat0)) * EARTH_RADIUS_M
    y = math.radians(lat) * EARTH_RADIUS_M
    return x, y


def _point_segment_m(p: Point, a: Point, b: Point) -> float:
    lat0 = p[0]
    px, py = _to_xy(p, lat0)
    ax, ay = _to_xy(a, lat0)
    bx, by = _to_xy(b, lat0)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_line_m(p: Point, line: Sequence[Point]) -> float:
    """Shortest distance in metres from a point to a polyline."""
    if not line:
        return math.inf
    if len(line) == 1:
        return _point_segment_m(p, line[0], line[0])
    return min(_point_segment_m(p, a, b) for a, b in itertools.pairwise(line))


def line_near_line(line: Sequence[Point], centre: Sequence[Point], buffer_m: float) -> bool:
    """True if any vertex of `line` is within buffer of `centre`, or the reverse.

    Checking vertices both ways catches crossings that happen between points,
    as long as the lines are sampled every few hundred metres, which holds for
    the feed's road geometry.
    """
    if not line or not centre:
        return False
    if any(distance_to_line_m(p, centre) <= buffer_m for p in line):
        return True
    return any(distance_to_line_m(p, line) <= buffer_m for p in centre)


def _road_ok(corridor: Corridor, road: str | None) -> bool:
    return not corridor.roads or road in corridor.roads


def segment_on_corridor(seg: Segment, corridor: Corridor) -> bool:
    if not _road_ok(corridor, seg.road):
        return False
    return line_near_line(seg.points, corridor.centreline, corridor.buffer_m)


def event_on_corridor(event: RoadEvent, corridor: Corridor) -> bool:
    return any(segment_on_corridor(s, corridor) for s in event.segments)


def sign_on_corridor(sign: VmsSign, corridor: Corridor) -> bool:
    if sign.lat is None or sign.lon is None or not _road_ok(corridor, sign.road):
        return False
    return distance_to_line_m((sign.lat, sign.lon), corridor.centreline) <= corridor.buffer_m


def match_events(events: Sequence[RoadEvent], corridor: Corridor) -> list[RoadEvent]:
    return [e for e in events if event_on_corridor(e, corridor)]


def match_signs(signs: Sequence[VmsSign], corridor: Corridor) -> list[VmsSign]:
    return [s for s in signs if sign_on_corridor(s, corridor)]

"""Which route number each of TfWM's route ids is.

TfWM's live feed names routes by an internal id ("1153"), not the number on the bus ("907"). Two ways to
translate, both tried by `routes_probe`:

1. A GTFS timetable whose `routes.txt` lists the same ids with their `route_short_name`: TfWM's own
   (`/gtfs/tfwm_gtfs.zip`), or BODS's GTFS for the West Midlands, which the same company (ITO World) builds.
2. The stops: the TfWM route whose trips call at the stops of the line's timetable (from BODS) is that line.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from typing import IO, Any


@dataclass(frozen=True)
class GtfsRoute:
    route_id: str
    short_name: str
    long_name: str
    agency: str


def _rows(z: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    member = next((i for i in z.namelist() if i.rsplit("/", 1)[-1].lower() == name), None)
    if member is None:
        return []
    with z.open(member) as raw:
        return list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")))


def read_routes(source: IO[bytes] | bytes) -> dict[str, GtfsRoute]:
    """routes.txt of a GTFS zip, by route_id. Raises zipfile.BadZipFile when it is not a zip."""
    with zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source) as z:
        agencies = {a.get("agency_id", ""): a.get("agency_name", "") for a in _rows(z, "agency.txt")}
        return {
            r["route_id"]: GtfsRoute(r["route_id"], (r.get("route_short_name") or "").strip(),
                                     (r.get("route_long_name") or "").strip(),
                                     agencies.get(r.get("agency_id", ""), r.get("agency_id", "")))
            for r in _rows(z, "routes.txt") if r.get("route_id")
        }  # fmt: skip


def stops_by_route(updates: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Every stop each live route id is predicted to call at, over all its trips in the feed."""
    out: dict[str, set[str]] = defaultdict(set)
    for u in updates:
        rid = u["trip"].get("route_id") or ""
        if rid:
            out[rid] |= {st["stop_id"] for st in u["stops"] if st.get("stop_id")}
    return out


def match_by_stops(updates: list[dict[str, Any]], line_stops: set[str], top: int = 3) -> list[tuple[str, float, int]]:
    """Route ids whose live trips call at the line's stops: (route_id, share of their stops on the line, shared).

    Scored by the share of the route's own stops that are on the line, so a long route that merely crosses it
    scores low; ties go to the one sharing more stops."""
    scored = []
    for rid, stops in stops_by_route(updates).items():
        shared = len(stops & line_stops)
        if shared:
            scored.append((rid, round(shared / len(stops), 2), shared))
    return sorted(scored, key=lambda t: (-t[1], -t[2]))[:top]

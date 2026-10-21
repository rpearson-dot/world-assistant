"""A map of Britain coloured by allergy risk, drawn as SVG from the stored pollen lattice.

Each patch of land takes a blend of the nearest forecast points' colours, the points are marked with
small circles, and the provider is credited, as its terms require. No location of anyone appears.
"""

from __future__ import annotations

import math
from html import escape
from typing import Any

from app.pollen_grid import km, on_land

LAT0, LAT1, LON0, LON1 = 49.8, 59.0, -8.3, 2.0
CELL = 0.1  # degrees: the size of a patch of colour
PX_PER_DEG = 78  # vertical; horizontal is scaled by cos(latitude) so shapes look right
COS = math.cos(math.radians(54.5))
SEA, LAND_UNKNOWN = "#a8c8e0", "#d9d9d9"
COLOURS = {"none": "#ffffff", "low": "#8cc700", "moderate": "#f5e400", "high": "#f7a600", "very high": "#e63a10"}
LEGEND = [("None", "none"), ("Low", "low"), ("Medium", "moderate"), ("High", "high"), ("Very high", "very high")]
CREDIT = "Allergy risk today. Austrian Pollen Information Service, www.polleninformation.at"


def _xy(lat: float, lon: float) -> tuple[float, float]:
    return (lon - LON0) * PX_PER_DEG * COS, (LAT1 - lat) * PX_PER_DEG


ORDER = list(COLOURS)  # none, low, moderate, high, very high


def _level(p: dict[str, Any]) -> float | None:
    labels = p.get("allergy_risk_label") or p.get("label") or []
    first = labels[0] if isinstance(labels, list) and labels else None
    return float(ORDER.index(first)) if first in COLOURS else None


def _hex(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def _colour(level: float) -> str:
    """A colour between the legend's stops for a level from 0 (none) to 4 (very high)."""
    level = max(0.0, min(4.0, level))
    i = min(3, int(level))
    a, b, t = _hex(COLOURS[ORDER[i]]), _hex(COLOURS[ORDER[i + 1]]), level - i
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))  # noqa: UP031


def _blend(points: list[dict[str, Any]], lat: float, lon: float) -> float | None:
    """Inverse-distance blend of the four nearest points that have a level."""
    near = sorted((km((lat, lon), (p["lat"], p["lon"])), lv) for p in points if (lv := _level(p)) is not None)[:4]
    if not near:
        return None
    if near[0][0] < 1.0:
        return near[0][1]
    wsum = sum(1 / d**2 for d, _ in near)
    return sum(lv / d**2 for d, lv in near) / wsum


def render_svg(points: list[dict[str, Any]]) -> str:
    """points: dicts with lat, lon and allergy_risk_label (or label), as from /v1/pollen/grid."""
    w, h = round((LON1 - LON0) * PX_PER_DEG * COS), round((LAT1 - LAT0) * PX_PER_DEG)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h + 22}" width="{w}" height="{h + 22}">',
           f'<rect width="{w}" height="{h}" fill="{SEA}"/>']  # fmt: skip
    ch = CELL * PX_PER_DEG
    if points:
        lat = LAT1 - CELL / 2
        while lat > LAT0:
            run_start: float | None = None
            run_colour = ""
            lon = LON0 + CELL / 2
            cells: list[tuple[float, str | None]] = []
            while lon < LON1:
                colour = None
                if on_land(lat, lon):
                    level = _blend(points, lat, lon)
                    colour = LAND_UNKNOWN if level is None else _colour(round(level * 4) / 4)  # 17 shades: small file
                cells.append((lon, colour))
                lon += CELL
            for lon, colour in [*cells, (LON1 + CELL, None)]:  # merge neighbours of one colour into one rectangle
                if colour != run_colour or run_start is None:
                    if run_start is not None and run_colour:
                        x, y = _xy(lat + CELL / 2, run_start - CELL / 2)
                        width = (lon - CELL / 2 - (run_start - CELL / 2)) * PX_PER_DEG * COS
                        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{width + 0.6:.1f}" height="{ch + 0.6:.1f}" fill="{run_colour}"/>')
                    run_start, run_colour = lon, colour or ""
            lat -= CELL
        for p in points:
            x, y = _xy(p["lat"], p["lon"])
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="none" stroke="#222" stroke-width="1"/>')
    lx = w - 104
    out.append(f'<rect x="{lx - 6}" y="6" width="100" height="{18 * len(LEGEND) + 8}" fill="#fff" stroke="#999"/>')
    for i, (name, key) in enumerate(LEGEND):
        y = 14 + i * 18
        out.append(f'<rect x="{lx}" y="{y}" width="14" height="12" fill="{COLOURS[key]}" stroke="#999"/>')
        out.append(f'<text x="{lx + 20}" y="{y + 10}" font-family="sans-serif" font-size="11" fill="#222">{escape(name)}</text>')
    out.append(f'<text x="6" y="{h + 15}" font-family="sans-serif" font-size="10" fill="#555">{escape(CREDIT)}</text>')
    out.append("</svg>")
    return "".join(out)

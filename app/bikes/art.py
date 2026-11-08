"""Generic pictures, used when a bike or part has no photo or brand picture of its own: a line drawing of the bike's
type (with a battery for an e-bike, a fork or shock when it has suspension, two saddles for a tandem), and an icon for
each kind of part. Plain SVG in currentColor, so they suit the light and dark themes."""

from __future__ import annotations

from typing import Any

_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" fill="none" stroke="currentColor" stroke-width="{w}" ' \
       'stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="{label}">{body}</svg>'  # fmt: skip


def bike(cfg: dict[str, Any], label: str = "bike") -> str:
    """A side view of a bike of this type: wheels, frame, bars, saddle, and what its answers add."""
    t = cfg.get("type", "road")
    tandem = t == "tandem"
    rx = 150 if tandem else 110  # rear wheel centre
    fx = 30
    r = 20 if t in ("hardtail", "full_suspension") else 19
    tyre_w = 4 if t in ("hardtail", "full_suspension", "gravel") else 2.5
    parts = [f'<circle cx="{fx}" cy="52" r="{r}" stroke-width="{tyre_w}"/>', f'<circle cx="{rx}" cy="52" r="{r}" stroke-width="{tyre_w}"/>',
             f'<circle cx="{fx}" cy="52" r="2"/><circle cx="{rx}" cy="52" r="2"/>']  # fmt: skip
    bb = 75 if not tandem else 112
    seat_top = (rx - 42, 20)
    # frame: rear triangle to the bottom bracket, seat tube, top and down tubes to the head tube
    head = (fx + 18, 22)
    parts.append(f'<path d="M{rx} 52 L{bb} 54 L{seat_top[0]} {seat_top[1]} L{rx} 52 M{bb} 54 L{head[0]} {head[1] + 6} '
                 f'M{seat_top[0]} {seat_top[1] + 2} L{head[0]} {head[1]}"/>')  # fmt: skip
    if tandem:  # the captain's bottom bracket and seat tube, and the second top tube
        parts.append(f'<path d="M66 54 L{bb} 54 M66 54 L72 20 L{seat_top[0]} {seat_top[1] + 2} M66 54 L{head[0]} {head[1] + 6}"/>')
        parts.append('<path d="M66 18 L78 18"/><circle cx="66" cy="54" r="5"/>')
    parts.append(f'<circle cx="{bb}" cy="54" r="5"/>')
    # fork: rigid, or suspension (stanchions)
    if cfg.get("front_suspension") == "fork":
        parts.append(f'<path d="M{head[0]} {head[1]} L{head[0] - 6} 38" stroke-width="4"/><path d="M{head[0] - 6} 38 L{fx} 52"/>')
    else:
        parts.append(f'<path d="M{head[0]} {head[1]} L{fx} 52"/>')
    if cfg.get("rear_suspension") == "shock":
        parts.append(f'<path d="M{seat_top[0] + 6} 34 L{bb + 12} 46" stroke-width="5"/>')
    # saddle(s) and bars: drops for road-ish bikes, flat for the rest, extensions for time trial
    parts.append(f'<path d="M{seat_top[0] - 7} {seat_top[1] - 2} L{seat_top[0] + 7} {seat_top[1] - 2}" stroke-width="3"/>')
    if tandem:
        parts.append('<path d="M60 16 L74 16" stroke-width="3"/>')
    if t in ("road", "gravel", "cyclocross", "tandem"):
        parts.append(f'<path d="M{head[0]} {head[1]} L{head[0] - 2} {head[1] - 6} q-9 0 -9 7 q0 6 5 6"/>')
    elif t == "tt":
        parts.append(f'<path d="M{head[0]} {head[1]} L{head[0] - 2} {head[1] - 6} L{head[0] - 16} {head[1] - 6} M{head[0] - 2} {head[1] - 6} L{head[0] - 20} {head[1] - 3}"/>')
    else:
        parts.append(f'<path d="M{head[0]} {head[1]} L{head[0] - 3} {head[1] - 7} L{head[0] - 13} {head[1] - 8}"/>')
    if cfg.get("ebike"):  # a battery on the down tube, and a bolt
        parts.append(f'<rect x="{bb - 26}" y="36" width="20" height="9" rx="2" transform="rotate(-38 {bb - 16} 40)" fill="currentColor" stroke="none" opacity="0.55"/>')
        parts.append(f'<path d="M{rx + 6} 12 l-4 7 h5 l-4 7" stroke-width="2"/>')
    width = rx + 26
    return _SVG.format(vb=f"0 0 {width} 76", w=2.5, label=label, body="".join(parts))


ICONS = {
    "chain": '<path d="M5 12a3 3 0 0 1 3-3h3a3 3 0 0 1 0 6H8a3 3 0 0 1-3-3zM13 12a3 3 0 0 1 3-3h1a3 3 0 0 1 0 6h-1"/>',
    "cassette": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="3"/>',
    "chainring": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>',
    "tyre": '<circle cx="12" cy="12" r="9" stroke-width="3"/><circle cx="12" cy="12" r="4"/>',
    "wheel": '<circle cx="12" cy="12" r="9"/><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6L5.6 18.4"/>',
    "rotor": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><circle cx="12" cy="6" r="1"/><circle cx="18" cy="12" r="1"/><circle cx="12" cy="18" r="1"/><circle cx="6" cy="12" r="1"/>',
    "pads": '<rect x="4" y="6" width="7" height="12" rx="2"/><rect x="13" y="6" width="7" height="12" rx="2"/>',
    "cable": '<path d="M3 18c4 0 4-12 9-12s5 12 9 12"/>',
    "drop": '<path d="M12 3c3 5 6 8 6 11a6 6 0 0 1-12 0c0-3 3-6 6-11z"/>',
    "gauge": '<path d="M4 16a8 8 0 1 1 16 0"/><path d="M12 16l4-5"/>',
    "hub": '<circle cx="12" cy="12" r="4"/><path d="M4 12h4M16 12h4"/><circle cx="12" cy="12" r="8"/>',
    "fork": '<path d="M9 3v8l-2 10M15 3v8l2 10M9 11h6" />',
    "shock": '<path d="M12 2v4M12 18v4M9 6h6v12H9z"/><path d="M9 9l6 2M9 12l6 2M9 15l6 2"/>',
    "dropper": '<path d="M12 6v14M8 4h8M10 12h4"/>',
    "battery": '<rect x="4" y="7" width="15" height="10" rx="2"/><path d="M21 10v4M8 10l-1 2h3l-1 2"/>',
    "motor": '<circle cx="12" cy="12" r="7"/><path d="M12 7l-2 5h4l-2 5"/>',
    "pedal": '<rect x="6" y="9" width="12" height="6" rx="1"/><path d="M12 9V4"/>',
    "saddle": '<path d="M3 10c4 0 6-2 10-2s8 2 8 4-4 2-8 2-6-2-10-2z"/><path d="M12 14v6"/>',
    "helmet": '<path d="M3 15a9 9 0 0 1 18 0zM3 15h18"/>',
    "shoe": '<path d="M3 16l1-8 6 2 4 4h5a2 2 0 0 1 2 2v1H3z"/>',
    "cleat": '<path d="M7 6h10l-2 12H9z"/>',
    "part": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9L7 7M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
}


def icon(name: str, label: str = "") -> str:
    return _SVG.format(vb="0 0 24 24", w=1.8, label=label or name, body=ICONS.get(name, ICONS["part"]))


def catalogue_icons() -> dict[str, str]:
    """Every part icon, for the page to use by kind."""
    return {k: icon(k) for k in ICONS}

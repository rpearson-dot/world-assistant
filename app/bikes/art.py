"""Generic pictures, used when a bike or part has no photo or brand picture of its own: a line drawing of the bike's
type (with a battery for an e-bike, a fork or shock when it has suspension, two saddles for a tandem), and an icon for
each kind of part. Plain SVG in currentColor, so they suit the light and dark themes."""

from __future__ import annotations

from typing import Any

_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" fill="none" stroke="currentColor" stroke-width="{w}" ' \
       'stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="{label}">{body}</svg>'  # fmt: skip


def _wheel(cx: float, cy: float, r: float, tyre: float, disc: bool, deep: bool = False) -> str:
    """A spoked wheel: tyre, rim, hub and spokes, with a rotor when it has a disc brake."""
    import math

    rim = r - tyre / 2 - 1.5
    spokes = "".join(f"M{cx:.1f} {cy:.1f} L{cx + rim * math.cos(a):.1f} {cy + rim * math.sin(a):.1f} "
                     for a in (i * math.pi / 8 + 0.1 for i in range(16)))  # fmt: skip
    out = [f'<circle cx="{cx}" cy="{cy}" r="{r}" stroke-width="{tyre}"/>',
           f'<circle cx="{cx}" cy="{cy}" r="{rim}" stroke-width="{4 if deep else 1}" opacity="{0.9 if deep else 0.7}"/>',
           f'<path d="{spokes}" stroke-width="0.6" opacity="0.45"/>', f'<circle cx="{cx}" cy="{cy}" r="2.5" fill="currentColor"/>']  # fmt: skip
    if disc:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="7" stroke-width="1.2"/>')
    return "".join(out)


# Real frame geometry per type (mm): stack and reach to the head tube's top, chainstay, bottom bracket drop, wheelbase,
# head and seat tube angles, head tube and seat tube length, wheel radius with its tyre, and the tyre's width
GEOMETRY = {
    "road": {'stack': 560, 'reach': 385, 'cs': 410, 'drop': 70, 'wb': 990, 'hta': 73, 'sta': 73.5, 'ht': 150, 'st': 530, 'r': 340, 'tyre': 25},
    "tt": {'stack': 500, 'reach': 410, 'cs': 405, 'drop': 70, 'wb': 990, 'hta': 73, 'sta': 78, 'ht': 110, 'st': 510, 'r': 340, 'tyre': 25},
    "gravel": {'stack': 595, 'reach': 380, 'cs': 425, 'drop': 75, 'wb': 1040, 'hta': 71, 'sta': 73.5, 'ht': 160, 'st': 520, 'r': 355, 'tyre': 42},
    "cyclocross": {'stack': 575, 'reach': 385, 'cs': 425, 'drop': 62, 'wb': 1020, 'hta': 71.5, 'sta': 73.5, 'ht': 145, 'st': 520, 'r': 348, 'tyre': 33},
    "hybrid": {'stack': 620, 'reach': 385, 'cs': 450, 'drop': 70, 'wb': 1080, 'hta': 71, 'sta': 73, 'ht': 170, 'st': 500, 'r': 352, 'tyre': 38},
    "hardtail": {'stack': 625, 'reach': 450, 'cs': 430, 'drop': 55, 'wb': 1180, 'hta': 65.5, 'sta': 75.5, 'ht': 110, 'st': 430, 'r': 375, 'tyre': 60},
    "full_suspension": {'stack': 630, 'reach': 460, 'cs': 435, 'drop': 30, 'wb': 1220, 'hta': 65, 'sta': 76.5, 'ht': 110, 'st': 420, 'r': 375, 'tyre': 60},
    "tandem": {'stack': 600, 'reach': 390, 'cs': 440, 'drop': 70, 'wb': 1750, 'hta': 72, 'sta': 73, 'ht': 180, 'st': 520, 'r': 345, 'tyre': 32},
}  # fmt: skip


# Particular bikes, for their picture (Bikes, Its answers: Model): their own geometry (a medium or 54 cm where sizes
# differ), wheel and tyre size and bars. Figures from published geometry where there is any (bikeinsights.com: the
# Reiver's M, the 2008 Trek 1 Series' 56 cm), otherwise typical of the bike and its year.
MODELS: dict[str, dict[str, Any]] = {
    "planet-x-reiver-2025": {"name": "Planet X Reiver (2025)", "type": "gravel",
                             "geometry": {"stack": 562, "reach": 379, "cs": 435, "drop": 69, "wb": 1036, "hta": 71, "sta": 73,
                                          "ht": 140, "st": 520, "r": 351, "tyre": 40}},
    "yeti-asr-sl-2005": {"name": "Yeti ASR-SL (2005, 26-inch)", "type": "full_suspension",
                         "geometry": {"stack": 565, "reach": 410, "cs": 425, "drop": 15, "wb": 1070, "hta": 70.5, "sta": 73,
                                      "ht": 110, "st": 445, "r": 333, "tyre": 52}},
    "trek-1.2-2008": {"name": "Trek 1.2 (2008)", "type": "road",
                      "geometry": {"stack": 576, "reach": 387, "cs": 410, "drop": 68, "wb": 983, "hta": 73.5, "sta": 73.6,
                                   "ht": 170, "st": 535, "r": 334, "tyre": 25}},
    "viking-tandem-26": {"name": "Viking tandem (26-inch)", "type": "tandem", "bars": "flat",
                         "geometry": {"stack": 600, "reach": 360, "cs": 440, "drop": 55, "wb": 1700, "hta": 71, "sta": 72,
                                      "ht": 170, "st": 480, "r": 333, "tyre": 50}},
}  # fmt: skip


def paint(name: Any) -> str | None:
    from app.themes import PAINTS

    got = PAINTS.get(str(name or ""))
    return got[1] if got else None


def bike(cfg: dict[str, Any], label: str = "bike") -> str:
    """A side view of a bike of this type, drawn from typical frame geometry for it (so a road bike, a gravel bike and
    a trail bike each look like one): spoked wheels with tyres as wide as the type's, the frame in the theme's accent
    colour, fork (suspension when it has one), bars, saddle and seat post (a dropper's thicker), crank, chain and rear
    mech, disc rotors, a full-suspension bike's linkage and shock, an e-bike's battery and motor, a tandem's stoker,
    and a hybrid's mudguards and rack."""
    import math

    t = cfg.get("type", "road")
    model = MODELS.get(str(cfg.get("model") or ""), {})
    gm = {**GEOMETRY.get(t, GEOMETRY["road"]), **model.get("geometry", {})}
    bars = model.get("bars") or ("drops" if t in ("road", "gravel", "cyclocross", "tandem") else "tt" if t == "tt" else "flat")
    main = paint(cfg.get("paint")) or "var(--accent)"  # the frame's paint, else the theme's accent
    second = paint(cfg.get("paint2")) or main  # fork and stays
    k, pad = 0.13, 12  # pixels a millimetre, and a margin
    rad = lambda a: math.radians(a)
    # in mm, bottom bracket at 0,0, y up
    rear = (-math.sqrt(gm["cs"] ** 2 - gm["drop"] ** 2), gm["drop"])
    front = (rear[0] + gm["wb"], gm["drop"])
    tandem, full = t == "tandem", t == "full_suspension"
    mtb = t in ("hardtail", "full_suspension")
    # a tandem: the stoker's bottom bracket (the drive) at 0,0 behind, the captain's in front of it, as far from the
    # front wheel as a solo bike's; the head tube and the captain's seat tube are measured from the captain's
    fb = (front[0] - (GEOMETRY["road"]["wb"] - GEOMETRY["road"]["cs"]), 0.0) if tandem else (0.0, 0.0)
    h_top = (fb[0] + gm["reach"], gm["stack"])
    h_low = (h_top[0] + gm["ht"] * math.cos(rad(gm["hta"])), h_top[1] - gm["ht"] * math.sin(rad(gm["hta"])))
    s_top = (-gm["st"] * math.cos(rad(gm["sta"])), gm["st"] * math.sin(rad(gm["sta"])))
    cap_bb = fb if tandem else None
    cap_top = (fb[0] + s_top[0], s_top[1]) if tandem else None
    xs = [rear[0] - gm["r"], front[0] + gm["r"], h_top[0] - 60]
    width_mm = max(xs) - min(xs)
    top_mm = max(h_top[1], s_top[1]) + 230
    bottom_mm = gm["drop"] - gm["r"] - 25  # below the tyres' bottom (and the crank)
    x0, y0 = min(xs), top_mm

    def P(pt: tuple[float, float]) -> str:
        return f"{(pt[0] - x0) * k + pad:.1f} {(y0 - pt[1]) * k + pad:.1f}"

    def xy(pt: tuple[float, float]) -> tuple[float, float]:
        return (pt[0] - x0) * k + pad, (y0 - pt[1]) * k + pad

    r = gm["r"] * k
    tyre = max(2.2, gm["tyre"] * k * 0.9)
    disc = lambda end: cfg.get(f"{end}_brake") in ("hydraulic_disc", "cable_disc", "cable_hydraulic")
    out: list[str] = []
    for c, end in ((rear, "rear"), (front, "front")):
        cx, cy = xy(c)
        out.append(_wheel(round(cx, 1), round(cy, 1), round(r, 1), round(tyre, 1), disc(end), t == "tt"))
    frame = [f"M{P(h_top)} L{P(h_low)}", f"M{P(h_low)} L{P(fb)}", f"M{P((0, 0))} L{P(s_top)}"]
    if tandem:  # top tubes over both seat tubes, the boom between the bottom brackets, the captain's seat tube
        frame += [f"M{P(h_top)} L{P(cap_top)} L{P(s_top)}", f"M{P(fb)} L{P((0, 0))}", f"M{P(fb)} L{P(cap_top)}",  # type: ignore[arg-type]
                  f"M{P(h_low)} L{P((0, 0))}"]  # the long diagonal: head tube to the stoker's bottom bracket
    else:
        frame.append(f"M{P(h_top)} L{P((s_top[0] * 0.93, s_top[1] * 0.93))}")
    seat_join = (s_top[0] * 0.9, s_top[1] * 0.9)
    if full:  # the rear triangle pivots above the bottom bracket; a link to the seat tube drives the shock
        pivot, link = (30, 90), (s_top[0] * 0.6, s_top[1] * 0.6)
        frame += [f"M{P(rear)} L{P(pivot)}", f"M{P(rear)} L{P((link[0] + 40, link[1] - 10))} L{P(link)}"]
        shock_a, shock_b = (h_low[0] * 0.45, h_low[1] * 0.45), (link[0] + 40, link[1] - 10)
    else:
        frame += [f"M{P(rear)} L{P((0, 0))}", f"M{P(rear)} L{P(seat_join)}"]
    stays = [f for f in frame if f.startswith(f"M{P(rear)}")]
    main_tubes = [f for f in frame if f not in stays]
    # a faint outline in the ink colour first, so a black frame shows on a dark page and a white one on a light page
    out.append(f'<path d="{" ".join(frame)}" stroke="currentColor" stroke-width="5.4" opacity="0.18"/>')
    out.append(f'<path d="{" ".join(main_tubes)}" stroke="{main}" stroke-width="3.6"/><path d="{" ".join(stays)}" stroke="{second}" stroke-width="3.2"/>')
    if full:
        out.append(f'<path d="M{P(shock_a)} L{P(shock_b)}" stroke-width="5"/><path d="M{P(shock_a)} L{P(shock_b)}" stroke-width="1.5" stroke="var(--card)"/>')
    # the fork: rigid (in the frame's colour), or suspension: stanchions sliding into thicker lowers
    if mtb or cfg.get("front_suspension") == "fork":
        mid = (h_low[0] + (front[0] - h_low[0]) * 0.45, h_low[1] + (front[1] - h_low[1]) * 0.45)
        out.append(f'<path d="M{P(h_low)} L{P(mid)}" stroke-width="2.6"/><path d="M{P(mid)} L{P(front)}" stroke-width="5"/>')
    else:
        bend = (h_low[0] + (front[0] - h_low[0]) * 0.55 - 8, h_low[1] + (front[1] - h_low[1]) * 0.55)
        out.append(f'<path d="M{P(h_low)} Q{P(bend)} {P(front)}" stroke="{second}" stroke-width="3"/>')
    # seat post(s) up the seat tube's line, and saddle(s)
    for top in ([s_top, cap_top] if tandem else [s_top]):
        sat = (top[0] - 190 * math.cos(rad(gm["sta"])), top[1] + 190 * math.sin(rad(gm["sta"])))  # type: ignore[index]
        post = 3.4 if cfg.get("dropper", "none") != "none" else 2
        out.append(f'<path d="M{P(top)} L{P(sat)}" stroke-width="{post}"/>'  # type: ignore[arg-type]
                   f'<path d="M{P((sat[0] - 120, sat[1] + 5))} Q{P((sat[0], sat[1] + 22))} {P((sat[0] + 125, sat[1] + 12))}" stroke-width="3.4"/>')
    if tandem:  # the stoker's bars, clamped to the captain's seat post
        sp = (cap_top[0] - 120 * math.cos(rad(gm["sta"])) + 40, cap_top[1] + 120 * math.sin(rad(gm["sta"])))  # type: ignore[index]
        out.append(f'<path d="M{P((sp[0] - 40, sp[1]))} L{P((sp[0] + 70, sp[1] + 10))} l1.5 5" stroke-width="2"/>')
    # stem and bars: drops on road-ish bikes, aero extensions on a time-trial bike, flat or riser bars on the rest
    stem_end = (h_top[0] + 95, h_top[1] + 20)
    out.append(f'<path d="M{P(h_top)} L{P((h_top[0] - 5, h_top[1] + 25))} L{P(stem_end)}" stroke-width="2.8"/>')
    if bars == "drops":
        flare = 25 if t == "gravel" else 0
        out.append(f'<path d="M{P(stem_end)} C{P((stem_end[0] + 90, stem_end[1]))} {P((stem_end[0] + 95, stem_end[1] - 130))} '
                   f'{P((stem_end[0] + 20 + flare, stem_end[1] - 125))} L{P((stem_end[0] - 30, stem_end[1] - 120))}" stroke-width="2.6"/>')
    elif bars == "tt":
        out.append(f'<path d="M{P(stem_end)} L{P((stem_end[0] + 200, stem_end[1] + 15))} M{P(stem_end)} '
                   f'C{P((stem_end[0] + 80, stem_end[1]))} {P((stem_end[0] + 60, stem_end[1] - 110))} {P((stem_end[0] + 10, stem_end[1] - 110))}" stroke-width="2.6"/>')
    else:
        out.append(f'<path d="M{P(stem_end)} L{P((stem_end[0] - 15, stem_end[1] + 35))} L{P((stem_end[0] - 70, stem_end[1] + 40))}" stroke-width="3"/>')
    # crank and chainring at the drive bottom bracket (the stoker's on a tandem), the chain to the cassette
    ring, cog = 95 * k, (40 if cfg.get("drive", "derailleur") == "derailleur" else 28) * k
    bx, by = xy((0, 0))
    rx_, ry_ = xy(rear)
    crank = lambda cx, cy: f'<path d="M{cx:.1f} {cy:.1f} l-8 20 m-4 0 h9" stroke-width="2.6"/>'
    if tandem:
        # the timing chain (on the far side, so dashed): the captain's single ring to a ring on the stoker's crank,
        # tensioned by the captain's eccentric bottom bracket; the stoker's two drive rings take the chain to the back
        timing = 70 * k
        cx, cy = xy(cap_bb)  # type: ignore[arg-type]
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{timing:.1f}" stroke-width="1.4"/>'
                   f'<circle cx="{cx + 1.2:.1f}" cy="{cy - 0.8:.1f}" r="{timing * 0.45:.1f}" stroke-width="1" opacity="0.8"/>'
                   f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{timing * 0.62:.1f}" stroke-width="1" opacity="0.5"/>' + crank(cx, cy))
        out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{timing:.1f}" stroke-width="1" stroke-dasharray="2 2" opacity="0.7"/>'
                   f'<path d="M{cx:.1f} {cy - timing:.1f} L{bx:.1f} {by - timing:.1f} M{cx:.1f} {cy + timing:.1f} L{bx:.1f} {by + timing:.1f}" '
                   f'stroke-width="1" stroke-dasharray="3 2" opacity="0.75"/>')
        out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{ring:.1f}" stroke-width="1.5"/>'
                   f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{ring * 0.78:.1f}" stroke-width="1.2"/>' + crank(bx, by))
    else:
        out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{ring:.1f}" stroke-width="1.5"/>' + crank(bx, by))
    out.append(f'<circle cx="{rx_:.1f}" cy="{ry_:.1f}" r="{cog:.1f}" stroke-width="1.3"/>')
    belt = cfg.get("drive") == "belt"
    out.append(f'<path d="M{bx:.1f} {by - ring:.1f} L{rx_:.1f} {ry_ - cog:.1f} M{bx:.1f} {by + ring:.1f} L{rx_:.1f} {ry_ + cog:.1f}" '
               f'stroke-width="{2 if belt else 1}" opacity="0.8"/>')
    if cfg.get("drive", "derailleur") == "derailleur":
        out.append(f'<path d="M{rx_ - 1:.1f} {ry_ + 2:.1f} l2 {10:.1f} l-5 3" stroke-width="1.6"/>')
    if cfg.get("ebike"):  # a battery along the down tube, a motor at the bottom bracket
        a, b = (h_low[0] * 0.25, h_low[1] * 0.25), (h_low[0] * 0.75, h_low[1] * 0.75)
        out.append(f'<path d="M{P(a)} L{P(b)}" stroke-width="8" opacity="0.45"/><circle cx="{bx:.1f}" cy="{by:.1f}" r="{ring * 1.2:.1f}" '
                   f'fill="currentColor" opacity="0.3" stroke="none"/>')
    if t == "hybrid":  # mudguards over both wheels and a rack over the back
        for c in (rear, front):
            cx, cy = xy(c)
            out.append(f'<path d="M{cx - r - 1:.1f} {cy - 4:.1f} A{r + 3:.1f} {r + 3:.1f} 0 0 1 {cx + r + 1:.1f} {cy - 4:.1f}" stroke-width="1.4"/>')
        top = xy((rear[0], s_top[1] * 0.75))
        out.append(f'<path d="M{xy(seat_join)[0]:.1f} {top[1]:.1f} L{rx_ - r * 0.7:.1f} {top[1]:.1f} L{rx_:.1f} {ry_:.1f}" stroke-width="1.6"/>')
    w = width_mm * k + 2 * pad
    h = (top_mm - bottom_mm) * k + 2 * pad
    return _SVG.format(vb=f"0 0 {w:.0f} {h:.0f}", w=2, label=label, body="".join(out))


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

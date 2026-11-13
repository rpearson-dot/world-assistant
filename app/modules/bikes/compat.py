"""Do the parts on a bike go together? Warnings, not refusals (a part's details may be missing or wrong): a 12-speed
cassette with an 11-speed chain, an XD cassette on an HG freehub, a Centre Lock rotor on a 6-bolt hub, a 180 mm rotor
where the bike says 160, mineral oil in a DOT brake, a tyre wider than the frame takes or for another wheel size.

Each check needs both details to be known; nothing is said when one is missing. `between()` checks one part against
the others on the same bike; `on_bike()` every pair on a bike, once.
"""

from __future__ import annotations

import re
from typing import Any

FREEHUBS = {"hg": "HG", "shimano": "HG", "sram": "HG", "xd": "XD", "xdr": "XDR", "micro": "Microspline", "ms": "Microspline",
            "n3w": "N3W", "campag": "Campagnolo", "campy": "Campagnolo"}  # fmt: skip
# what each freehub body takes besides its own cassettes: road HG 11-speed bodies take HG 8-10 speed with a spacer, an
# XDR takes an XD cassette with a 1.85 mm spacer
TAKES = {"XDR": {"XD"}}


def _int(v: Any) -> int | None:
    m = re.search(r"\d+", str(v or ""))
    return int(m.group()) if m else None


def _num(v: Any) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
    return float(m.group()) if m else None


def freehub(v: Any) -> str | None:
    t = re.sub(r"[^a-z0-9]", "", str(v or "").lower())
    if not t:
        return None
    for key in sorted(FREEHUBS, key=len, reverse=True):
        if t.startswith(key):
            return FREEHUBS[key]
    return str(v).strip()


def mount(v: Any) -> str | None:
    t = str(v or "").lower()
    if "centre" in t or "center" in t or "cl" == t.strip():
        return "Centre Lock"
    if "6" in t or "six" in t or "bolt" in t:
        return "6-bolt"
    return None


def fluid(v: Any) -> str | None:
    t = str(v or "").lower()
    return "DOT" if "dot" in t else "mineral" if "mineral" in t or "oil" in t else None


def end_of(p: dict[str, Any]) -> str | None:
    """front or rear: the wheel it is in, else its own position."""
    e = p.get("end") or p.get("position")
    return e if e in ("front", "rear") else None


def between(part: dict[str, Any], others: list[dict[str, Any]], cfg: dict[str, Any] | None = None) -> list[str]:
    """What does not go together between `part` and the other parts on the same bike (and the bike's answers)."""
    cfg = cfg or {}
    d = part.get("details") or {}
    kind, end = part["kind"], end_of(part)
    out: list[str] = []

    def same(k: str, at_end: bool = False) -> list[dict[str, Any]]:
        return [o for o in others if o["kind"] == k and o.get("id") != part.get("id") and (not at_end or end_of(o) == end)]

    if kind in ("cassette", "chain"):  # speeds
        mine = _int(d.get("speeds"))
        other = "chain" if kind == "cassette" else "cassette"
        for o in same(other):
            theirs = _int((o.get("details") or {}).get("speeds"))
            if mine and theirs and mine != theirs:
                out.append(f"{mine}-speed {kind} with {_a(theirs)}-speed {other}: they need the same speeds")
        want = _int(cfg.get("speeds"))
        if mine and want and mine != want:
            out.append(f"{mine}-speed {kind} on a bike with {want}-speed shifting")
    if kind == "cassette" or kind == "hub":  # freehub body
        for o in (same("hub", True) if kind == "cassette" else same("cassette", True)):
            cas, hub = (d, o.get("details") or {}) if kind == "cassette" else (o.get("details") or {}, d)
            fc, fh = freehub(cas.get("freehub")), freehub(hub.get("freehub"))
            if fc and fh and fc != fh and fc not in TAKES.get(fh, set()):
                out.append(f"{fc} cassette on a {fh} freehub: it will not fit (change the freehub body or the cassette)")
    if kind in ("rotor", "hub"):  # rotor mount
        for o in (same("hub", True) if kind == "rotor" else same("rotor", True)):
            rot, hub = (d, o.get("details") or {}) if kind == "rotor" else (o.get("details") or {}, d)
            mr, mh = mount(rot.get("mount")), mount(hub.get("rotor_mount"))
            if mr == "Centre Lock" and mh == "6-bolt":
                out.append("Centre Lock rotor on a 6-bolt hub: it will not fit")
            elif mr == "6-bolt" and mh == "Centre Lock":
                out.append("6-bolt rotor on a Centre Lock hub: it needs an adapter")
    if kind == "rotor" and end:
        size, want = _num(d.get("diameter_mm")), _num(cfg.get(f"{end}_rotor_mm"))
        if size and want and size != want:
            out.append(f"{size:g} mm {end} rotor where the bike's {end} brake is set up for {want:g} mm "
                       "(the calliper's adapter decides)")  # fmt: skip
    if kind == "brake_fluid" and end:
        f, want = fluid(d.get("fluid")), fluid(cfg.get(f"{end}_fluid"))
        if f and want and f != want:
            out.append(f"{f} fluid for a {want} {end} brake: never mix them (the seals swell)")
    if kind == "tyre":
        width, room = _num(d.get("width_mm")), _num(cfg.get("max_tyre_mm"))
        if width and room and width > room:
            out.append(f"{width:g} mm tyre on a bike that takes up to {room:g} mm")
        bead, want = _int(d.get("bead_mm")), _int(cfg.get("wheel_size"))
        if bead and want and bead != want:
            from app.modules.bikes.defaults import WHEELS

            out.append(f"{WHEELS.get(bead, f'{bead} mm')} tyre on a bike with {WHEELS.get(want, f'{want} mm')} wheels")
        for o in same("tyre"):
            ob = _int((o.get("details") or {}).get("bead_mm"))
            if bead and ob and bead != ob and not want and end == "front":  # said once, from the front
                out.append(f"The tyres are for different wheel sizes ({bead} and {ob} mm beads)")
    return out


def _a(n: int) -> str:
    return f"an {n}" if str(n).startswith(("8", "11", "18")) else f"a {n}"


def on_bike(parts: list[dict[str, Any]], cfg: dict[str, Any] | None = None) -> list[str]:
    """Everything on one bike that does not go together, each said once."""
    seen: list[str] = []
    for p in parts:
        for w in between(p, parts, cfg):
            key = _key(w)
            if key not in {_key(x) for x in seen}:
                seen.append(w)
    return seen


def _key(w: str) -> frozenset[str]:
    """The same pair said from either side ("12-speed cassette with an 11-speed chain" and the other way round)."""
    return frozenset(x for x in re.findall(r"[\w-]+", w.split(":")[0].lower()) if x not in ("a", "an", "with", "on"))

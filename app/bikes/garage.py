"""The garage game: where each part sits on its bike's drawing, what could replace it (spares that fit and things to
buy), and a service list of jobs (replace, service, bin, buy) done in one go.
"""

from __future__ import annotations

from typing import Any

from app.bikes import compat, placement
from app.bikes import defaults as D
from app.bikes import groupsets as GS

# where a kind of part is on the drawing; "{end}" is its wheel (or its own front or rear)
SPOT = {"chain": "chain", "lube": "chain", "timing_chain": "timing", "timing_rings": "timing", "eccentric_bb": "timing",
        "cassette": "cassette", "hub_gear": "cassette", "chainrings": "crank", "crankset": "crank", "bottom_bracket": "crank",
        "pedals": "crank", "belt": "chain", "jockey_wheels": "rear_derailleur", "rear_derailleur": "rear_derailleur",
        "hanger": "rear_derailleur", "front_derailleur": "front_derailleur", "gear_cables": "bars", "brake_cable": "bars",
        "levers": "bars", "shifters": "bars", "brake_levers": "bars", "bar_tape": "bars", "coin_cells": "bars",
        "di2_battery": "frame", "headset": "headset", "susp_stem": "headset", "fork_lowers": "fork", "fork_damper": "fork",
        "shock_air": "shock", "shock_damper": "shock", "susp_post": "seatpost", "dropper_lower": "seatpost",
        "dropper_full": "seatpost", "saddle": "saddle", "ebike_motor": "crank", "ebike_battery": "frame",
        "axs_battery": "rear_derailleur", "disc_pads": "{end}_brake", "rim_pads": "{end}_brake", "calliper": "{end}_brake",
        "brake_fluid": "{end}_brake", "calliper_service": "{end}_brake", "rotor": "{end}_brake", "drum_brake": "{end}_brake",
        "tyre": "{end}_tyre", "tube": "{end}_tyre", "sealant": "{end}_tyre", "tyre_check": "{end}_tyre",
        "rim": "{end}_rim", "spokes": "{end}_rim", "hub": "{end}_wheel"}  # fmt: skip


def spot_of(part: dict[str, Any], end: str | None, spots: dict[str, Any]) -> str:
    """The drawing's place for a part (the frame when the drawing has no place for it, a shock on a hardtail)."""
    s = SPOT.get(part["kind"], "frame")
    if "{end}" in s:
        s = s.format(end=end or part.get("position") or "rear")
    return s if s in spots else "frame"


def _speeds(part: dict[str, Any]) -> int | None:
    return compat._int((part.get("details") or {}).get("speeds"))


def buy_options(part: dict[str, Any], cfg: dict[str, Any], on_bike: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What to buy to replace a part: the same again, the bike's groupset's, then others that fit (same speeds and,
    for a cassette, the freehub); for pads and rotors the calliper they go in. Each is a line for the shopping list."""
    kind = part["kind"]
    name = D.KINDS.get(kind, D.KINDS["other"]).name
    out: list[dict[str, Any]] = []

    def add(brand: str | None, model: str | None, why: str, details: dict[str, Any] | None = None) -> None:
        text = " ".join(x for x in (brand, model) if x) or name
        if kind != "other" and not any(w in text.lower() for w in name.lower().replace("(", "").split() if len(w) > 3):
            text = f"{text} {name.lower()}"
        if text.lower() not in {o["text"].lower() for o in out}:
            out.append({"text": text, "brand": brand, "model": model, "why": why, "details": details or {}})

    if part.get("brand") or part.get("model"):
        add(part.get("brand"), part.get("model"), "the same again", {k: v for k, v in (part.get("details") or {}).items() if k != "last"})
    own = GS.GROUPSETS.get(str(cfg.get("groupset") or ""))
    if own and GS.model_for(own, kind, _disc(cfg, part)):
        add(own.brand, GS.model_for(own, kind, _disc(cfg, part)), f"{own.brand} {own.name}, the bike's groupset", GS.details_for(own, kind))
    speeds = _speeds(part) or compat._int(cfg.get("speeds"))
    hub = next((p for p in on_bike if p["kind"] == "hub" and compat.end_of(p) == "rear"), None)
    freehub = compat.freehub(((hub or {}).get("details") or {}).get("freehub")) or (own.freehub if own else None)
    if kind in GS.SPEED_KINDS:
        for g in GS.GROUPSETS.values():
            m = GS.model_for(g, kind, _disc(cfg, part))
            if not m or (speeds and g.speeds != speeds) or (kind == "cassette" and freehub and g.freehub != freehub
                                                             and g.freehub not in compat.TAKES.get(freehub, set())):  # fmt: skip
                continue
            if kind in ("levers", "shifters", "rear_derailleur") and own and g.brand != own.brand:
                continue  # shifting does not mix makers
            add(g.brand, m, f"fits: {g.speeds}-speed" + (f", {g.freehub} freehub" if kind == "cassette" else ""), GS.details_for(g, kind))
            if len(out) >= 8:
                break
    if kind in ("disc_pads", "rim_pads"):
        cal = next((p for p in on_bike if p["kind"] == "calliper" and compat.end_of(p) == compat.end_of(part) and p.get("model")), None)
        if cal:
            add(cal.get("brand"), f"pads for {cal['model']}", "for the calliper on the bike")
    if kind == "tyre":
        size = (part.get("details") or {}).get("size")
        if size:
            add(None, f"{size} tyre", "the same size")
    if not out:
        add(None, None, "any that fits")
    return out


def _disc(cfg: dict[str, Any], part: dict[str, Any]) -> bool:
    ends = [compat.end_of(part)] if compat.end_of(part) else ["front", "rear"]
    return any(cfg.get(f"{e}_brake") in D.DISC_BRAKES for e in ends)


def spare_options(part: dict[str, Any], shelf: list[dict[str, Any]], bike: dict[str, Any] | None, wheel: dict[str, Any] | None,
                  on_bike: list[dict[str, Any]]) -> list[dict[str, Any]]:  # fmt: skip
    """The spares on the shelf that could replace a part: the same kind, each with why it cannot go (empty: it can) and
    what would not go with the rest of the bike. Those that fit first."""
    cfg = (bike or {}).get("config") or {}
    others = [p for p in on_bike if p["id"] != part["id"]]
    out = []
    for s in shelf:
        if s["kind"] != part["kind"]:
            continue
        cand = {**s, "position": part.get("position"), "id": s["id"]}
        cant = placement.problems(cand, bike=None if wheel else bike, wheel=wheel, wheel_bike=bike if wheel else None)
        warn = compat.between({**cand, "end": compat.end_of(part)}, others, cfg)
        out.append({"id": s["id"], "name": s.get("name") or s.get("label"), "describe": s.get("describe"), "quantity": s.get("quantity", 1),
                    "brand": s.get("brand"), "model": s.get("model"), "problems": cant, "warnings": warn})  # fmt: skip
    return sorted(out, key=lambda x: (bool(x["problems"]), len(x["warnings"])))

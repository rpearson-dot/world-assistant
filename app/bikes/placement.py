"""Where a part can go: checked before it is fitted, so the garage cannot say something impossible (a cassette on a
front wheel, a chain on a wheel, a rotor on a rim-brake wheel, two tyres on one wheel).

`problems()` gives the reasons a part cannot go somewhere (empty: it can); `wheel_warnings()` what looks wrong when a
whole wheel goes onto a bike (its parts were fitted for another), which is said but not refused.
"""

from __future__ import annotations

from typing import Any

from app.bikes import defaults as D

REAR_ONLY = {"cassette": "a cassette", "hub_gear": "a hub gear"}  # only ever on a rear wheel
DISC = {"hydraulic_disc", "cable_disc", "cable_hydraulic"}
RIM = {"rim", "v_brake"}
MANY = {"other", "axs_battery", "coin_cells"}  # can be more than one in the same place
# what a part needs of the bike it is on: (the answer, the values that allow it, why not), checked at its end
NEEDS: dict[str, tuple[str, set[str], str]] = {
    "rotor": ("{end}_brake", DISC, "a rotor needs a disc brake"),
    "disc_pads": ("{end}_brake", DISC, "disc pads need a disc brake"),
    "rim": ("{end}_brake", RIM, "rim wear is for rim brakes"),
    "rim_pads": ("{end}_brake", RIM, "rim pads need rim brakes"),
    "drum_brake": ("{end}_brake", {"drum"}, "a drum brake service needs a drum or roller brake"),
    "brake_fluid": ("{end}_brake", {"hydraulic_disc", "cable_hydraulic"}, "brake fluid needs a hydraulic brake"),
    "sealant": ("{end}_tyre", {"tubeless"}, "sealant is for a tubeless tyre"),
    "tube": ("{end}_tyre", {"tubed", "latex"}, "an inner tube is for a tubed tyre"),
    "cassette": ("drive", {"derailleur", "single"}, "a cassette (or single cog) needs derailleur gears or a single speed"),
    "hub_gear": ("drive", {"hub"}, "a hub gear service needs a hub gear"),
    "chain": ("drive", {"derailleur", "single", "hub"}, "a chain needs a chain drive (this bike has a belt)"),
    "belt": ("drive", {"belt"}, "a belt needs a belt drive"),
    "gear_cables": ("shifting", {"mechanical"}, "gear cables are for mechanical shifting"),
    "coin_cells": ("shifting", {"axs"}, "shifter coin cells are for SRAM AXS"),
    "di2_battery": ("shifting", {"di2"}, "a Di2 battery needs Shimano Di2"),
    "fork_lowers": ("front_suspension", {"fork"}, "a fork service needs a suspension fork"),
    "fork_damper": ("front_suspension", {"fork"}, "a fork service needs a suspension fork"),
    "susp_stem": ("front_suspension", {"stem"}, "this is for a suspension stem or headset"),
    "shock_air": ("rear_suspension", {"shock"}, "a shock service needs a rear shock"),
    "shock_damper": ("rear_suspension", {"shock"}, "a shock service needs a rear shock"),
    "susp_post": ("rear_suspension", {"post"}, "this is for a suspension seatpost"),
    "dropper_lower": ("dropper", {"cable", "hydraulic", "wireless"}, "a dropper service needs a dropper post"),
    "dropper_full": ("dropper", {"cable", "hydraulic", "wireless"}, "a dropper service needs a dropper post"),
}  # fmt: skip


def _end(part: dict[str, Any], wheel: dict[str, Any] | None) -> str | None:
    if wheel and wheel.get("kind") == "wheel":
        return wheel.get("position")
    pos = part.get("position")
    return pos if pos in ("front", "rear") else None


def problems(part: dict[str, Any], *, bike: dict[str, Any] | None = None, wheel: dict[str, Any] | None = None,
             wheel_bike: dict[str, Any] | None = None, there: list[dict[str, Any]] | None = None) -> list[str]:  # fmt: skip
    """Why `part` cannot go onto `bike` (directly) or into `wheel` (a set; wheel_bike is the bike it is on, if any).
    `there` is what is already fitted in that place (other parts). Empty when it can."""
    kind = part["kind"]
    k = D.KINDS.get(kind, D.KINDS["other"])
    name = k.name.lower()
    out: list[str] = []
    if bike is None and wheel is None:
        return out  # off: onto the shelf or into kit
    if wheel is not None and wheel.get("kind") == "wheel":
        if k.on == "bike" and kind != "other":
            out.append(f"{k.name} goes on the bike, not on a wheel")
        if k.on == "rider" and kind != "other":
            out.append(f"{k.name} is your kit, not part of a wheel")
        if kind in REAR_ONLY and wheel.get("position") == "front":
            out.append(f"{REAR_ONLY[kind].capitalize()} only goes on a rear wheel")
    if bike is not None and wheel is None:
        if k.on == "wheel":
            out.append(f"{k.name} goes on a wheel: put it on the bike's {'rear' if kind in REAR_ONLY else part.get('position') or 'front or rear'} wheel")
        if kind in ("helmet", "shoes", "cleats"):
            out.append(f"{k.name} is your kit, not part of a bike")
        if kind == "axs_battery":
            cfg = bike.get("config") or {}
            if cfg.get("shifting") != "axs" and cfg.get("dropper") != "wireless":
                out.append(f"{bike.get('label', 'This bike')} has no AXS shifting or wireless dropper: change its answers first")
    cfg = ((bike or wheel_bike) or {}).get("config") or {}
    need = NEEDS.get(kind)
    if cfg and need and not out:
        key = need[0].format(end=_end(part, wheel) or "")
        if "{end}" in need[0] and not _end(part, wheel):
            key = ""
        value = cfg.get(key) if key else None
        if key and value is not None and value not in need[1]:
            label = ((bike or wheel_bike) or {}).get("label", "the bike")
            out.append(f"{need[2].capitalize()}: {label}'s {key.replace('_', ' ')} is {str(value).replace('_', ' ')} "
                       "(change the bike's answers if that is wrong)")  # fmt: skip
    if kind not in MANY and there:
        end = _end(part, wheel)
        same = [p for p in there if p["kind"] == kind and p["id"] != part.get("id")
                and (wheel is not None or (p.get("position") or None) == (part.get("position") or None))]  # fmt: skip
        if same:
            where = f"the {wheel['label']}" if wheel is not None else (f"{bike['label']}" if bike else "there")  # type: ignore[index]
            what = f"{name} ({end})" if end and wheel is None else name
            out.append(f"There is already {('an ' if what[0] in 'aeiou' else 'a ') + what} on {where}: replace it, or move "
                       "that one off first")  # fmt: skip
    return out


def wheel_warnings(wheel: dict[str, Any], parts: list[dict[str, Any]], bike: dict[str, Any]) -> list[str]:
    """What looks wrong when a whole wheel goes onto a bike: its parts checked against that bike's answers."""
    out = []
    for p in parts:
        for why in problems(p, wheel=wheel, wheel_bike=bike):
            out.append(f"{wheel['label']}: {why}")
    return out

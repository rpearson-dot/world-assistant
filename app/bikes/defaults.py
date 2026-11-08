"""What a bike is made of: each kind of part with its default life, each bike type's default answers, and the parts
those answers give. One editable place for the defaults (docs/bikes.md has the reasoning and sources).

Everything here is a starting point: every answer, part and life can be changed on the page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Kind:
    name: str  # as shown when a part has no label of its own
    km: float | None = None  # life by distance
    hours: float | None = None  # by hours ridden
    days: float | None = None  # by time
    on: str = "bike"  # bike | wheel | rider (kit, batteries)
    wet: bool = False  # a wet ride counts extra
    indoor: bool = False  # indoor (trainer) rides count
    measure: str = ""  # what to check rather than trust the count ("measure the chain's stretch")
    icon: str = "part"
    action: str = "replaced"  # what is done when it is due: replaced, serviced, checked, charged, topped_up or bled

    @property
    def bought(self) -> bool:
        """Something to buy when it is due (so it can go on the Outdoors list), rather than a service or a check."""
        return self.action == "replaced"


# Typical lives (docs/bikes.md has the sources). km is the distance; hours ridden for suspension and batteries.
KINDS: dict[str, Kind] = {
    "chain": Kind("Chain", km=3500, wet=True, indoor=True, icon="chain",
                  measure="measure its stretch: replace at 0.5% (11- and 12-speed) or 0.75% (10-speed and below)"),
    "timing_chain": Kind("Timing chain", km=15000, wet=True, indoor=True, icon="chain",
                         measure="its stretch, and its tension at the eccentric bottom bracket"),
    "timing_rings": Kind("Timing chainrings", km=30000, wet=True, indoor=True, icon="chainring"),
    "eccentric_bb": Kind("Eccentric bottom bracket (timing chain tension)", days=180, icon="chainring", action="checked",
                         measure="the timing chain's slack: about 1 cm up and down midway; turn the eccentric to take it up"),
    "lube": Kind("Chain lube or wax", km=250, wet=True, indoor=True, icon="drop", action="serviced"),
    "cassette": Kind("Cassette", km=10000, on="wheel", wet=True, indoor=True, icon="cassette"),
    "chainrings": Kind("Chainrings", km=20000, wet=True, indoor=True, icon="chainring"),
    "jockey_wheels": Kind("Jockey wheels", km=12000, wet=True, indoor=True, icon="cassette"),
    "belt": Kind("Belt and sprockets", km=20000, icon="chain"),
    "hub_gear": Kind("Hub gear service", days=730, on="wheel", icon="hub", action="serviced"),
    "gear_cables": Kind("Gear cables and housing", km=5000, days=365, icon="cable"),
    "disc_pads": Kind("Disc brake pads", km=2000, wet=True, icon="pads",
                      measure="check every 500 km: replace at 0.5 mm of pad material"),
    "rim_pads": Kind("Rim brake pads", km=3000, wet=True, icon="pads", measure="replace at the wear line"),
    "rotor": Kind("Brake rotor", km=8000, on="wheel", icon="rotor",
                  measure="replace below its minimum thickness (Shimano 1.5 mm, SRAM 1.55 mm)"),
    "rim": Kind("Rim (rim brake wear)", km=20000, on="wheel", icon="tyre", measure="the rim's wear indicator"),
    "brake_cable": Kind("Brake cable and housing", km=5000, days=365, icon="cable"),
    "brake_fluid": Kind("Brake fluid (bleed)", days=365, icon="drop", action="bled"),
    "calliper_service": Kind("Calliper service", days=730, icon="pads", action="serviced"),
    "drum_brake": Kind("Drum or roller brake service", days=730, on="wheel", icon="hub", action="serviced"),
    "tyre": Kind("Tyre", km=4500, on="wheel", icon="tyre", measure="the wear indicator, a flat crown or cuts; and age (about 5 years)"),
    "tube": Kind("Inner tube", on="wheel", icon="tyre"),
    "tyre_check": Kind("Tyre pressure check", days=7, on="wheel", icon="gauge", action="checked"),
    "sealant": Kind("Tubeless sealant", days=90, on="wheel", icon="drop", action="topped_up"),
    "hub": Kind("Hub bearings and freehub", km=8000, days=365, on="wheel", icon="hub", action="serviced"),
    "spokes": Kind("Spoke tension and true", days=365, on="wheel", icon="wheel", action="checked"),
    "bottom_bracket": Kind("Bottom bracket", km=15000, icon="chainring"),
    "headset": Kind("Headset bearings", days=365, icon="part", action="serviced"),
    "bar_tape": Kind("Bar tape or grips", days=365, icon="part"),
    "pedals": Kind("Pedal bearings", days=540, icon="pedal", action="serviced"),
    "saddle": Kind("Saddle", icon="saddle"),
    "hanger": Kind("Derailleur hanger (alignment)", days=365, icon="part", action="checked"),
    "fork_lowers": Kind("Fork lower-leg service", hours=50, days=365, icon="fork", action="serviced"),
    "fork_damper": Kind("Fork spring and damper service", hours=200, days=730, icon="fork", action="serviced"),
    "shock_air": Kind("Shock air can service", hours=50, days=365, icon="shock", action="serviced"),
    "shock_damper": Kind("Shock damper service", hours=200, days=730, icon="shock", action="serviced"),
    "susp_stem": Kind("Suspension stem or headset service", days=365, icon="fork", action="serviced"),
    "susp_post": Kind("Suspension seatpost service", days=365, icon="shock", action="serviced"),
    "dropper_lower": Kind("Dropper post lower service", hours=50, days=365, icon="dropper", action="serviced"),
    "dropper_full": Kind("Dropper post full service", hours=200, days=730, icon="dropper", action="serviced"),
    "coin_cells": Kind("Shifter coin cells (CR2032)", days=365, icon="battery"),
    "axs_battery": Kind("AXS battery", hours=20, on="rider", indoor=True, icon="battery", action="charged"),
    "di2_battery": Kind("Di2 battery", km=1000, icon="battery", action="charged"),
    "ebike_motor": Kind("E-bike motor service", days=365, icon="motor", action="serviced"),
    "ebike_battery": Kind("E-bike battery check", days=365, icon="battery", action="checked"),
    "helmet": Kind("Helmet", days=1825, on="rider", icon="helmet", measure="replace after any crash"),
    "shoes": Kind("Shoes", on="rider", icon="shoe"),
    "cleats": Kind("Pedal cleats", days=540, on="rider", icon="cleat"),
    "other": Kind("Other", icon="part"),
}

BIKE_TYPES = {
    "road": "Road", "gravel": "Gravel", "cyclocross": "Cyclocross", "hardtail": "Hardtail MTB",
    "full_suspension": "Full-suspension MTB", "hybrid": "Hybrid or commuter", "tt": "Time trial", "tandem": "Tandem",
}  # fmt: skip
BRAKES = {
    "hydraulic_disc": "Hydraulic disc", "cable_disc": "Mechanical (cable-pull) disc",
    "cable_hydraulic": "Cable-actuated hydraulic", "rim": "Rim calliper", "v_brake": "V-brake or cantilever",
    "drum": "Drum, roller or coaster", "none": "None",
}  # fmt: skip
FRONT_SUSPENSION = {"none": "None", "fork": "Suspension fork", "stem": "Suspension stem or headset"}
REAR_SUSPENSION = {"none": "None", "shock": "Rear shock", "post": "Suspension seatpost"}
DROPPERS = {"none": "None", "cable": "Cable dropper", "hydraulic": "Hydraulic dropper", "wireless": "Wireless (AXS)"}
SHIFTING = {"mechanical": "Mechanical (cables)", "axs": "SRAM AXS", "di2": "Shimano Di2", "none": "Single speed"}
DRIVE = {"derailleur": "Derailleur gears", "single": "Single speed", "hub": "Hub gear", "belt": "Belt drive"}
CHOICES = {"type": BIKE_TYPES, "front_brake": BRAKES, "rear_brake": BRAKES, "front_suspension": FRONT_SUSPENSION,
           "rear_suspension": REAR_SUSPENSION, "dropper": DROPPERS, "shifting": SHIFTING, "drive": DRIVE,
           "front_fluid": {"mineral": "Mineral oil", "dot": "DOT"}, "rear_fluid": {"mineral": "Mineral oil", "dot": "DOT"},
           "bottom_bracket": {"threaded": "Threaded", "pressfit": "Press-fit"},
           "pedals": {"clipless": "Clipless", "flat": "Flat"},
           "front_tyre": {"tubed": "Tubed (butyl)", "latex": "Tubed (latex)", "tubeless": "Tubeless"},
           "rear_tyre": {"tubed": "Tubed (butyl)", "latex": "Tubed (latex)", "tubeless": "Tubeless"}}  # fmt: skip

_BASE: dict[str, Any] = {"ebike": False, "drive": "derailleur", "shifting": "mechanical", "front_rings": 2,
                         "front_brake": "hydraulic_disc", "rear_brake": "hydraulic_disc", "front_fluid": "mineral",
                         "rear_fluid": "mineral", "front_suspension": "none", "rear_suspension": "none", "dropper": "none",
                         "front_tyre": "tubed", "rear_tyre": "tubed", "front_width": 28, "rear_width": 28,
                         "bottom_bracket": "threaded", "pedals": "clipless"}  # fmt: skip
TYPE_DEFAULTS: dict[str, dict[str, Any]] = {
    "road": {},
    "gravel": {"front_rings": 1, "front_tyre": "tubeless", "rear_tyre": "tubeless", "front_width": 40, "rear_width": 40},
    "cyclocross": {"front_rings": 1, "front_tyre": "tubeless", "rear_tyre": "tubeless", "front_width": 33, "rear_width": 33},
    "hardtail": {"front_rings": 1, "front_suspension": "fork", "dropper": "cable", "front_tyre": "tubeless",
                 "rear_tyre": "tubeless", "front_width": 61, "rear_width": 61, "pedals": "flat"},
    "full_suspension": {"front_rings": 1, "front_suspension": "fork", "rear_suspension": "shock", "dropper": "cable",
                        "front_tyre": "tubeless", "rear_tyre": "tubeless", "front_width": 61, "rear_width": 61,
                        "pedals": "flat"},
    "hybrid": {"front_rings": 3, "front_brake": "v_brake", "rear_brake": "v_brake", "front_width": 35, "rear_width": 35,
               "pedals": "flat"},
    "tt": {"front_brake": "rim", "rear_brake": "rim", "front_width": 25, "rear_width": 25},
    "tandem": {"front_width": 32, "rear_width": 32, "pedals": "flat"},
}  # fmt: skip
SURFACE = {"road": 1.0, "tt": 1.0, "hybrid": 1.0, "tandem": 1.0, "gravel": 0.85, "cyclocross": 0.8, "hardtail": 1.0,
           "full_suspension": 1.0}  # fmt: skip


def defaults_for(bike_type: str, ebike: bool = False) -> dict[str, Any]:
    """The default answers for a bike type (all changeable)."""
    if bike_type not in BIKE_TYPES:
        raise ValueError(f"no bike type {bike_type!r} (one of {', '.join(BIKE_TYPES)})")
    return {**_BASE, **TYPE_DEFAULTS[bike_type], "type": bike_type, "ebike": bool(ebike)}


@dataclass
class Planned:
    kind: str
    on: str  # bike | front | rear (a wheel)
    position: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _brake(end: str, brake: str, fluid: str) -> list[Planned]:
    wheel = end
    out: list[Planned] = []
    if brake in ("hydraulic_disc", "cable_disc", "cable_hydraulic"):
        out += [Planned("disc_pads", "bike", end), Planned("rotor", wheel, end)]
    if brake == "hydraulic_disc":
        out.append(Planned("brake_fluid", "bike", end, {"fluid": fluid}))
    if brake in ("cable_disc", "cable_hydraulic", "rim", "v_brake"):
        out.append(Planned("brake_cable", "bike", end))
    if brake == "cable_hydraulic":
        out.append(Planned("calliper_service", "bike", end))
    if brake in ("rim", "v_brake"):
        out += [Planned("rim_pads", "bike", end), Planned("rim", wheel, end)]
    if brake == "drum":
        out.append(Planned("drum_brake", wheel, end))
    return out


def _tyre(end: str, tyre: str, width: float) -> list[Planned]:
    tubeless = tyre == "tubeless"
    out = [Planned("tyre", end, end, {"tubeless": tubeless, "tube": None if tubeless else tyre, "width_mm": width}),
           Planned("tyre_check", end, end, {"every_days": 1 if tyre == "latex" else 7})]
    out.append(Planned("sealant", end, end) if tubeless else Planned("tube", end, end, {"tube": tyre}))
    return out


def planned_parts(cfg: dict[str, Any]) -> list[Planned]:
    """The parts a bike's answers give it: on the bike, or on its front or rear wheel."""
    c = {**_BASE, **cfg}
    out: list[Planned] = []
    drive = c["drive"]
    if drive in ("derailleur", "single", "hub"):
        out += [Planned("chain", "bike"), Planned("lube", "bike"), Planned("chainrings", "bike")]
    if drive == "belt":
        out.append(Planned("belt", "bike"))
    if drive == "derailleur":
        out += [Planned("cassette", "rear", "rear"), Planned("jockey_wheels", "bike"), Planned("hanger", "bike")]
    if drive == "hub":
        out.append(Planned("hub_gear", "rear", "rear"))
    if drive == "single":
        out.append(Planned("cassette", "rear", "rear", {"single_cog": True}))
    if drive == "derailleur" and c["shifting"] == "mechanical":
        out.append(Planned("gear_cables", "bike"))
    if c["shifting"] == "axs":
        out += [Planned("coin_cells", "bike")]  # the derailleur takes a labelled AXS battery (rider kit)
    if c["shifting"] == "di2":
        out.append(Planned("di2_battery", "bike"))
    for end in ("front", "rear"):
        out += _brake(end, c[f"{end}_brake"], c.get(f"{end}_fluid", "mineral"))
        out += _tyre(end, c[f"{end}_tyre"], float(c[f"{end}_width"]))
        out += [Planned("hub", end, end), Planned("spokes", end, end)]
    fs, rs = c["front_suspension"], c["rear_suspension"]
    if fs == "fork":
        out += [Planned("fork_lowers", "bike", "front"), Planned("fork_damper", "bike", "front")]
    if fs == "stem":
        out.append(Planned("susp_stem", "bike", "front"))
    if rs == "shock":
        out += [Planned("shock_air", "bike", "rear"), Planned("shock_damper", "bike", "rear")]
    if rs == "post":
        out.append(Planned("susp_post", "bike", "rear"))
    if c["dropper"] in ("cable", "hydraulic", "wireless"):
        out += [Planned("dropper_lower", "bike"), Planned("dropper_full", "bike")]
    out += [Planned("bottom_bracket", "bike", None, {"pressfit": c["bottom_bracket"] == "pressfit"}),
            Planned("headset", "bike"), Planned("bar_tape", "bike"), Planned("pedals", "bike")]  # fmt: skip
    if c["type"] == "tandem":
        # The timing chain joins the captain's single ring to a ring on the stoker's crank (the left side); the drive
        # chainrings are the stoker's, on the right, so the bike's chainrings are the stoker's. The captain's bottom
        # bracket is eccentric: turning it tensions the timing chain (a long one: often two chains joined).
        out += [Planned("timing_chain", "bike", None, {"links": "long: often two chains joined"}),
                Planned("timing_rings", "bike"), Planned("eccentric_bb", "bike", "captain"),
                Planned("bottom_bracket", "bike", "stoker"), Planned("pedals", "bike", "stoker"),
                Planned("saddle", "bike", "stoker")]  # fmt: skip
    if c.get("ebike"):
        out += [Planned("ebike_motor", "bike"), Planned("ebike_battery", "bike")]
    return out


def life_of(kind: str, details: dict[str, Any] | None = None) -> dict[str, float]:
    """A kind's default life, adjusted for the part's details (press-fit bottom brackets, latex tubes)."""
    k = KINDS.get(kind, KINDS["other"])
    life = {u: v for u, v in (("km", k.km), ("hours", k.hours), ("days", k.days)) if v}
    d = details or {}
    if kind == "bottom_bracket" and d.get("pressfit"):
        life["km"] = 10000
    if kind == "tyre_check" and d.get("every_days"):
        life["days"] = float(d["every_days"])
    return life


def tyre_pressure(width_mm: float, tubeless: bool, bike_type: str, system_kg: float, end: str,
                  max_psi: float | None = None) -> float:  # fmt: skip
    """A starting tyre pressure in psi, front or rear: wider tyres, tubeless and rough surfaces lower; more weight
    higher (about 45% of the rider and bike on the front, 55% on the rear). A starting point to adjust, calibrated to
    common calculators' figures for 90 kg together (rear, tubed: 85 psi at 25 mm, 72 at 28, 55 at 35)."""
    psi = 85.0 * (25.0 / max(width_mm, 18.0)) ** 1.3  # rear, tubed, 90 kg, road
    psi *= (max(system_kg, 30.0) / 90.0) ** 0.7
    if end == "front":
        psi *= (0.45 / 0.55) ** 0.7
    if tubeless:
        psi *= 0.92
    psi *= SURFACE.get(bike_type, 1.0)
    psi = round(psi)
    return float(min(psi, max_psi) if max_psi else psi)


def bar(psi: float) -> float:
    return round(psi / 14.5038, 1)


# Each kind's model details (beyond brand and model), for the page to ask for, and to carry over on a replacement with
# the same again (or to change, for something different). Names are keys in a part's details; units in the label.
SPECS: dict[str, list[tuple[str, str]]] = {
    "chain": [("speeds", "Speeds"), ("links", "Links")],
    "timing_chain": [("links", "Links")],
    "timing_rings": [("teeth", "Teeth (each ring)")],
    "cassette": [("speeds", "Speeds"), ("range", "Range (e.g. 10-44)"), ("freehub", "Freehub (HG, XD, XDR, Microspline)")],
    "chainrings": [("teeth", "Teeth (e.g. 42 or 50/34)"), ("bcd", "Mount (BCD or direct)"), ("speeds", "Speeds")],
    "jockey_wheels": [("teeth", "Teeth")],
    "belt": [("length", "Length (teeth)")],
    "disc_pads": [("compound", "Compound (resin, metal)"), ("shape", "Pad shape (e.g. L05A)")],
    "rim_pads": [("compound", "Compound")],
    "rotor": [("diameter_mm", "Diameter mm"), ("mount", "Mount (6-bolt, Centre Lock)"), ("min_mm", "Minimum thickness mm")],
    "brake_fluid": [("fluid", "Fluid (mineral, DOT 4, DOT 5.1)")],
    "tyre": [("size", "Size (e.g. 700x40c, 29x2.4)"), ("width_mm", "Width mm"), ("compound", "Compound or casing"),
             ("tubeless", "Tubeless (yes/no)")],
    "tube": [("tube", "Tube (butyl, latex, TPU)"), ("valve", "Valve (Presta, Schrader)")],
    "sealant": [("product", "Sealant"), ("ml", "Amount ml")],
    "hub": [("bearings", "Bearings")],
    "bottom_bracket": [("standard", "Standard (BSA, T47, BB86, PF30...)"), ("spindle", "Spindle (24 mm, DUB, 30 mm)")],
    "headset": [("standard", "Standard (e.g. ZS44/EC44)")],
    "pedals": [("model", "Pedal system (SPD, SPD-SL, Look)")],
    "cleats": [("float", "Float (e.g. 6°)")],
    "gear_cables": [("type", "Cable type")],
    "brake_cable": [("type", "Cable type")],
    "fork_lowers": [("model", "Fork (e.g. Pike Ultimate 140 mm)")],
    "shock_air": [("model", "Shock (e.g. Deluxe Ultimate 210x55)")],
    "dropper_lower": [("travel_mm", "Travel mm")],
    "axs_battery": [("bought", "Bought (month)")],
    "helmet": [("size", "Size"), ("made", "Made (date on the label)")],
    "bar_tape": [("colour", "Colour")],
}

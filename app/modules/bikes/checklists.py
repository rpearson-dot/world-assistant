"""Each bike's checklists: a quick one before a ride and a fuller one for a service, made from its answers (an AXS
bike's batteries, a tandem's timing chain, a tubeless bike's sealant), plus any items of your own. Ticking a list off
logs it; items that are the garage's own checks (the tyre pressures, the hanger, the spokes, a tandem's timing chain
tension) also restart those parts' counts, as logging the check would.
"""

from __future__ import annotations

from typing import Any

DISC = {"hydraulic_disc", "cable_disc", "cable_hydraulic"}
NAMES = {"pre": "Pre-ride check", "service": "Service check"}


def _item(id_: str, text: str, checks: str | None = None) -> dict[str, Any]:
    return {"id": id_, "text": text, "checks": checks}


def lists(cfg: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The two lists for a bike with these answers. `checks` is the kind of part the item is the check for."""
    c = cfg or {}
    brakes = {c.get("front_brake"), c.get("rear_brake")}
    disc, rim = bool(brakes & DISC), bool(brakes & {"rim", "v_brake"})
    tubeless = "tubeless" in (c.get("front_tyre"), c.get("rear_tyre"))
    tandem = c.get("type") == "tandem"
    susp = c.get("front_suspension") in ("fork", "stem") or c.get("rear_suspension") in ("shock", "post")
    pre = [_item("tyres", "Tyres: pressures, and no cuts or bulges", "tyre_check"),
           _item("brakes", "Brakes: both levers firm, and they stop the bike"),
           _item("wheels", "Wheels: thru-axles or quick releases tight; no wobble"),
           _item("chain", "Chain: clean and lubed, no stiff links")]  # fmt: skip
    if c.get("drive", "derailleur") in ("derailleur", "hub"):
        pre.append(_item("gears", "Gears: shift through them"))
    if c.get("shifting") == "axs" or c.get("dropper") == "wireless":
        pre.append(_item("axs", "AXS batteries charged (or a spare with you)"))
    if c.get("shifting") == "di2":
        pre.append(_item("di2", "Di2 battery charged"))
    if c.get("dropper") in ("cable", "hydraulic", "wireless"):
        pre.append(_item("dropper", "Dropper goes down and comes back up"))
    if susp:
        pre.append(_item("sag", "Suspension: air pressures and sag"))
    if tandem:
        pre += [_item("timing", "Timing chain: tension (about 1 cm up and down midway)", "eccentric_bb"),
                _item("stoker", "Stoker's bars, seatpost and pedals tight")]  # fmt: skip
    if c.get("ebike"):
        pre.append(_item("ebattery", "E-bike battery charged and locked in"))
    pre += [_item("bolts", "Bars, stem and saddle tight; headset no play"),
            _item("lights", "Lights and bike computer charged")]  # fmt: skip

    service = [_item("stretch", "Measure the chain's stretch (log the reading)"),
               _item("drivetrain", "Clean the drivetrain; check the cassette and chainrings for hooked teeth")]  # fmt: skip
    if disc:
        service += [_item("pads", "Disc pads: at least 0.5 mm of pad left"),
                    _item("rotors", "Rotors: true and above their minimum thickness")]  # fmt: skip
    if rim:
        service += [_item("rimpads", "Rim pads: above the wear line, toed in"), _item("rims", "Rims: wear indicators")]
    if c.get("shifting", "mechanical") == "mechanical" or brakes & {"rim", "v_brake", "cable_disc", "cable_hydraulic"}:
        service.append(_item("cables", "Cables and housing: no fraying or kinks"))
    if c.get("drive", "derailleur") == "derailleur":
        service.append(_item("hanger", "Derailleur hanger straight", "hanger"))
    service += [_item("spokes", "Wheels: true, spokes evenly tight", "spokes"),
                _item("bearings", "Hubs, bottom bracket, headset and pedals: no play or grinding"),
                _item("tyrewear", "Tyres: tread, cuts and age")]  # fmt: skip
    if tubeless:
        service.append(_item("sealant", "Tubeless sealant: still sloshing (top up if not)"))
    if susp:
        service.append(_item("stanchions", "Suspension: clean the stanchions, check for oil and play"))
    if tandem:
        service += [_item("timingwear", "Timing chain: stretch, and the eccentric's bolts tight", "eccentric_bb"),
                    _item("stokerbits", "Stoker's cranks, bars and seatpost: no play")]  # fmt: skip
    service += [_item("torque", "Bolts to their torque (stem, bars, seatpost, calipers, rotors)"),
                _item("frame", "Frame and fork: no cracks or dents")]  # fmt: skip
    own = c.get("checklist") or {}
    for key, items in (("pre", pre), ("service", service)):
        for n, t in enumerate(own.get(key) or []):
            if str(t).strip():
                items.append(_item(f"own{n}", str(t).strip()[:120]))
    return {"pre": pre, "service": service}


def summary(name: str, items: list[dict[str, Any]], done: list[str]) -> str:
    """What is logged for a list: "Pre-ride check: all 9 done", or which were not."""
    left = [i["text"].split(":")[0] for i in items if i["id"] not in done]
    if not left:
        return f"{name}: all {len(items)} done"
    return f"{name}: {len(items) - len(left)} of {len(items)} done (not: {', '.join(left)})"

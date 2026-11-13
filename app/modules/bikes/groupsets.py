"""Groupsets: a starting point for a bike's drivetrain and brakes. Choosing one fills in the make and model of each of
its parts on the bike (levers or shifters, derailleurs, crankset, cassette, chain, callipers) with the details that
matter for what goes with what (speeds, the cassette's freehub, the brake fluid), and sets the bike's answers to match
(the shifting, one or two rings, the speeds).

Model numbers are the makers' own. Bikes are often sold with a mix (a cheaper cassette or crankset), so any part can be
changed afterwards; filling in only the parts with no make yet keeps what has already been said.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Groupset:
    name: str
    brand: str
    speeds: int
    rings: int  # 1 or 2 (3 for a triple)
    shifting: str  # mechanical, axs, di2
    freehub: str
    bars: str  # drop or flat
    fluid: str | None  # a hydraulic brake's fluid: mineral (Shimano) or dot (SRAM)
    parts: dict[str, Any] = field(default_factory=dict)  # kind: model, or {"rim": model, "disc": model}
    year: str = ""


def _road(name: str, sp: int, sh: str, fh: str, **parts: Any) -> Groupset:
    return Groupset(name, "Shimano", sp, 2, sh, fh, "drop", "mineral", parts)


# fmt: off
GROUPSETS: dict[str, Groupset] = {
    # --- Shimano road and gravel
    "shimano-sora-r3000": _road("Sora R3000 (9-speed)", 9, "mechanical", "HG", levers="ST-R3000", rear_derailleur="RD-R3000",
                                front_derailleur="FD-R3000", crankset="FC-R3000", cassette="CS-HG400-9", chain="CN-HG53",
                                calliper={"rim": "BR-R3000"}),
    "shimano-sora-3400": _road("Sora 3400 (9-speed, about 2008)", 9, "mechanical", "HG", levers="ST-3400",
                               rear_derailleur="RD-3400", front_derailleur="FD-3400", cassette="CS-HG50-9", chain="CN-HG53"),
    "shimano-tiagra-4700": _road("Tiagra 4700 (10-speed)", 10, "mechanical", "HG", levers="ST-4700", rear_derailleur="RD-4700",
                                 front_derailleur="FD-4700", crankset="FC-4700", cassette="CS-HG500-10", chain="CN-HG54",
                                 calliper={"rim": "BR-4700"}),
    "shimano-105-r7000": _road("105 R7000 (11-speed)", 11, "mechanical", "HG", levers={"rim": "ST-R7000", "disc": "ST-R7020"},
                               rear_derailleur="RD-R7000", front_derailleur="FD-R7000", crankset="FC-R7000", cassette="CS-R7000",
                               chain="CN-HG601", calliper={"rim": "BR-R7000", "disc": "BR-R7070"}),
    "shimano-105-r7100": _road("105 R7100 (12-speed)", 12, "mechanical", "HG", levers={"disc": "ST-R7120"}, rear_derailleur="RD-R7100",
                               front_derailleur="FD-R7100", crankset="FC-R7100", cassette="CS-R7101", chain="CN-M7100",
                               calliper={"disc": "BR-R7170"}),
    "shimano-105-di2-r7100": _road("105 Di2 R7100 (12-speed)", 12, "di2", "HG", levers={"disc": "ST-R7170"},
                                   rear_derailleur="RD-R7150", front_derailleur="FD-R7150", crankset="FC-R7100",
                                   cassette="CS-R7101", chain="CN-M7100", calliper={"disc": "BR-R7170"}),
    "shimano-ultegra-r8000": _road("Ultegra R8000 (11-speed)", 11, "mechanical", "HG", levers={"rim": "ST-R8000", "disc": "ST-R8020"},
                                   rear_derailleur="RD-R8000", front_derailleur="FD-R8000", crankset="FC-R8000",
                                   cassette="CS-R8000", chain="CN-HG701", calliper={"rim": "BR-R8000", "disc": "BR-R8070"}),
    "shimano-ultegra-di2-r8100": _road("Ultegra Di2 R8100 (12-speed)", 12, "di2", "HG", levers={"disc": "ST-R8170"},
                                       rear_derailleur="RD-R8150", front_derailleur="FD-R8150", crankset="FC-R8100",
                                       cassette="CS-R8101", chain="CN-M8100", calliper={"disc": "BR-R8170"}),
    "shimano-grx-rx810-1x": Groupset("GRX RX810 1x (11-speed)", "Shimano", 11, 1, "mechanical", "HG", "drop", "mineral",
                                     {"levers": {"disc": "ST-RX810"}, "rear_derailleur": "RD-RX812", "crankset": "FC-RX810-1",
                                      "cassette": "CS-M8000", "chain": "CN-HG701", "calliper": {"disc": "BR-RX810"}}),
    "shimano-grx-rx810-2x": Groupset("GRX RX810 2x (11-speed)", "Shimano", 11, 2, "mechanical", "HG", "drop", "mineral",
                                     {"levers": {"disc": "ST-RX810"}, "rear_derailleur": "RD-RX810", "front_derailleur": "FD-RX810",
                                      "crankset": "FC-RX810-2", "cassette": "CS-HG800-11", "chain": "CN-HG701",
                                      "calliper": {"disc": "BR-RX810"}}),
    "shimano-grx-rx820-1x": Groupset("GRX RX820 1x (12-speed)", "Shimano", 12, 1, "mechanical", "Microspline", "drop", "mineral",
                                     {"levers": {"disc": "ST-RX820"}, "rear_derailleur": "RD-RX822", "crankset": "FC-RX820-1",
                                      "cassette": "CS-M8100", "chain": "CN-M8100", "calliper": {"disc": "BR-RX820"}}),
    # --- Shimano mountain bike
    "shimano-xt-m760": Groupset("Deore XT M760 (9-speed, about 2005)", "Shimano", 9, 3, "mechanical", "HG", "flat", "mineral",
                                {"shifters": "SL-M760", "rear_derailleur": "RD-M760", "front_derailleur": "FD-M760",
                                 "crankset": "FC-M760", "cassette": "CS-M760", "chain": "CN-HG93",
                                 "brake_levers": {"disc": "BL-M765"}, "calliper": {"disc": "BR-M765"}}, "2003-2006"),
    "shimano-deore-m6100": Groupset("Deore M6100 (12-speed)", "Shimano", 12, 1, "mechanical", "Microspline", "flat", "mineral",
                                    {"shifters": "SL-M6100", "rear_derailleur": "RD-M6100", "crankset": "FC-M6100",
                                     "cassette": "CS-M6100", "chain": "CN-M6100", "brake_levers": {"disc": "BL-M6100"},
                                     "calliper": {"disc": "BR-M6100"}}),
    "shimano-slx-m7100": Groupset("SLX M7100 (12-speed)", "Shimano", 12, 1, "mechanical", "Microspline", "flat", "mineral",
                                  {"shifters": "SL-M7100", "rear_derailleur": "RD-M7100", "crankset": "FC-M7100",
                                   "cassette": "CS-M7100", "chain": "CN-M7100", "brake_levers": {"disc": "BL-M7100"},
                                   "calliper": {"disc": "BR-M7100"}}),
    "shimano-xt-m8100": Groupset("Deore XT M8100 (12-speed)", "Shimano", 12, 1, "mechanical", "Microspline", "flat", "mineral",
                                 {"shifters": "SL-M8100", "rear_derailleur": "RD-M8100", "crankset": "FC-M8100",
                                  "cassette": "CS-M8100", "chain": "CN-M8100", "brake_levers": {"disc": "BL-M8100"},
                                  "calliper": {"disc": "BR-M8100"}}),
    # --- SRAM
    "sram-force-10": Groupset("Force (10-speed, about 2010)", "SRAM", 10, 2, "mechanical", "HG", "drop", None,
                              {"levers": {"rim": "Force DoubleTap", "disc": "Force DoubleTap"}, "rear_derailleur": "Force",
                               "front_derailleur": "Force", "crankset": {"model": "Force GXP", "details": {"spindle": "GXP"}},
                               "cassette": "PG-1070", "chain": "PC-1091", "calliper": {"rim": "Force"}}, "2007-2012"),
    "sram-apex-1": Groupset("Apex 1 (11-speed)", "SRAM", 11, 1, "mechanical", "HG", "drop", "dot",
                            {"levers": {"disc": "Apex 1 HRD", "rim": "Apex 1 DoubleTap"}, "rear_derailleur": "Apex 1",
                             "crankset": "Apex 1", "cassette": "PG-1130", "chain": "PC-1110", "calliper": {"disc": "Apex 1 HRD"}}),
    "sram-apex-axs-xplr": Groupset("Apex eTap AXS XPLR (12-speed)", "SRAM", 12, 1, "axs", "HG", "drop", "dot",
                                   {"levers": {"disc": "Apex eTap AXS HRD"}, "rear_derailleur": "Apex XPLR eTap AXS",
                                    "crankset": "Apex 1 DUB", "cassette": "XPLR PG-1231 11-44", "chain": "Apex Flattop",
                                    "calliper": {"disc": "Apex eTap AXS HRD flat mount"}}),
    "sram-rival-axs-xplr": Groupset("Rival eTap AXS XPLR 1x (12-speed)", "SRAM", 12, 1, "axs", "XDR", "drop", "dot",
                                    {"levers": {"disc": "Rival eTap AXS HRD"}, "rear_derailleur": "Rival XPLR eTap AXS",
                                     "crankset": "Rival 1 DUB", "cassette": "XG-1251 XPLR 10-44", "chain": "Rival Flattop",
                                     "calliper": {"disc": "Rival eTap AXS HRD flat mount"}}),
    "sram-rival-axs-2x": Groupset("Rival eTap AXS 2x (12-speed)", "SRAM", 12, 2, "axs", "XDR", "drop", "dot",
                                  {"levers": {"disc": "Rival eTap AXS HRD"}, "rear_derailleur": "Rival eTap AXS",
                                   "front_derailleur": "Rival eTap AXS", "crankset": "Rival DUB 2x", "cassette": "XG-1250 10-36",
                                   "chain": "Rival Flattop", "calliper": {"disc": "Rival eTap AXS HRD flat mount"}}),
    "sram-rival-axs-e1-xplr": Groupset("Rival AXS E1 XPLR 1x (13-speed, 2025)", "SRAM", 13, 1, "axs", "XDR", "drop", "dot",
                                      {"levers": {"disc": "Rival AXS E1 HRD"}, "rear_derailleur": {"model": "Rival XPLR AXS E1", "details": {"max_cog": 46}},
                                       "crankset": {"model": "Rival 1x DUB", "details": {"teeth": "42", "length_mm": 170, "spindle": "DUB"}},
                                       "cassette": {"model": "XG-1351 10-46", "details": {"range": "10-46"}},
                                       "chain": {"model": "Rival AXS E1", "details": {"links": 120}},
                                       "calliper": {"disc": "Rival AXS E1 HRD flat mount"},
                                       "rotor": {"model": "Avid Centerline", "details": {"diameter_mm": 160, "mount": "Centre Lock"}}},
                                      "2025: on the Planet X Reiver Rival build"),
    "sram-apex-axs-xplr-reiver": Groupset("Apex AXS XPLR 1x (12-speed), Planet X Reiver build", "SRAM", 12, 1, "axs", "HG", "drop", "dot",
                                          {"levers": {"disc": "Apex eTap AXS HRD"}, "rear_derailleur": {"model": "Apex XPLR eTap AXS", "details": {"max_cog": 44}},
                                           "crankset": {"model": "Apex 1 Wide DUB", "details": {"teeth": "40", "length_mm": 172.5, "spindle": "DUB"}},
                                           "cassette": {"model": "XPLR PG-1231 11-44", "details": {"range": "11-44"}},
                                           "chain": {"model": "Apex 12-speed", "details": {"links": 120}},
                                           "calliper": {"disc": "Apex eTap AXS HRD flat mount"},
                                           "rotor": {"model": "Avid Centerline", "details": {"diameter_mm": 160, "mount": "Centre Lock"}}},
                                          "2025: on the Planet X Reiver Apex build"),
    "sram-force-axs-xplr": Groupset("Force eTap AXS XPLR 1x (12-speed)", "SRAM", 12, 1, "axs", "XDR", "drop", "dot",
                                    {"levers": {"disc": "Force eTap AXS HRD"}, "rear_derailleur": "Force XPLR eTap AXS",
                                     "crankset": "Force 1 DUB", "cassette": "XG-1271 XPLR 10-44", "chain": "Force Flattop",
                                     "calliper": {"disc": "Force eTap AXS HRD flat mount"}}),
    "sram-nx-eagle": Groupset("NX Eagle (12-speed)", "SRAM", 12, 1, "mechanical", "HG", "flat", None,
                              {"shifters": "NX Eagle trigger", "rear_derailleur": {"model": "NX Eagle", "details": {"max_cog": 50}},
                               "crankset": "NX Eagle DUB", "cassette": {"model": "PG-1230 11-50", "details": {"range": "11-50"}},
                               "chain": "NX Eagle"}),
    "sram-gx-eagle": Groupset("GX Eagle (12-speed)", "SRAM", 12, 1, "mechanical", "XD", "flat", None,
                              {"shifters": "GX Eagle trigger", "rear_derailleur": "GX Eagle", "crankset": "GX Eagle DUB",
                               "cassette": "XG-1275 10-52", "chain": "GX Eagle"}),
    "sram-gx-eagle-axs": Groupset("GX Eagle AXS (12-speed)", "SRAM", 12, 1, "axs", "XD", "flat", None,
                                  {"shifters": "AXS Controller", "rear_derailleur": "GX Eagle AXS", "crankset": "GX Eagle DUB",
                                   "cassette": "XG-1275 10-52", "chain": "GX Eagle"}),
}

# fmt: on
SPEED_KINDS = {"levers", "shifters", "rear_derailleur", "cassette", "chain", "chainrings"}


def model_for(g: Groupset, kind: str, disc: bool) -> str | None:
    m = g.parts.get(kind)
    if isinstance(m, dict):
        return m.get("model") or m.get("disc" if disc else "rim") or None
    return m


def _extra(g: Groupset, kind: str) -> dict[str, Any]:
    m = g.parts.get(kind)
    return dict(m.get("details") or {}) if isinstance(m, dict) else {}


def answers(g: Groupset, cfg: dict[str, Any]) -> dict[str, Any]:
    """The bike's answers changed to match the groupset: its shifting, rings, speeds and bars, and a hydraulic
    brake's fluid."""
    out: dict[str, Any] = {"groupset": None, "drive": "derailleur", "shifting": g.shifting, "front_rings": g.rings,
                           "speeds": g.speeds, "bars": g.bars}  # fmt: skip
    for end in ("front", "rear"):
        if g.fluid and cfg.get(f"{end}_brake") == "hydraulic_disc":
            out[f"{end}_fluid"] = g.fluid
    return out


def details_for(g: Groupset, kind: str) -> dict[str, Any]:
    d: dict[str, Any] = _extra(g, kind)
    if kind in SPEED_KINDS:
        d["speeds"] = g.speeds
    if kind == "cassette":
        d["freehub"] = g.freehub
    if kind == "rotor" and "rotor" not in g.parts:
        return {}
    if kind == "brake_fluid" and g.fluid:
        d["fluid"] = "DOT 5.1" if g.fluid == "dot" else "mineral"
    return d


def catalogue() -> list[dict[str, Any]]:
    return [{"id": k, "name": f"{g.brand} {g.name}", "brand": g.brand, "speeds": g.speeds, "rings": g.rings,
             "shifting": g.shifting, "bars": g.bars, "freehub": g.freehub, "year": g.year,
             "parts": sorted(g.parts)} for k, g in GROUPSETS.items()]  # fmt: skip

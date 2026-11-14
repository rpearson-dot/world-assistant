"""What a device on your network probably is, and what World Assistant could do with it. Pure.

From what the Network module gathered about each device: the company that made its network adapter (the IEEE
register, by the first half of its MAC address), the name the router gave it, the domains it looks up (Pi-hole) and
the ports it talks to (the firewall log). Each kind of device is a rule; a device matching one is named with how sure
and why, and with the module that suits it:

- a built-in module that is off ("turn on Air conditioning for it");
- one that is on but not set up for this device ("add it to Air conditioning: its address is 10.0.2.51");
- one already looking after it ("Air conditioning has it");
- or none yet (an outside module could; or it is simply named).

Nothing is changed: each suggestion links to the module's own page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Kind:
    id: str
    what: str
    vendors: tuple[str, ...] = ()  # words in the adapter maker's name
    names: tuple[str, ...] = ()  # words in the router's name for it
    domains: tuple[str, ...] = ()  # domain endings it looks up
    ports: tuple[int, ...] = ()
    module: str | None = None  # a built-in module that suits it
    note: str = ""
    weak_vendor: bool = False  # the maker alone says little (Espressif chips are in everything)
    extra: dict[str, Any] = field(default_factory=dict, compare=False)


KINDS: tuple[Kind, ...] = (
    Kind("airstage", "a Fujitsu air conditioner (Airstage)", vendors=("fujitsu general",), names=("airstage", "fujitsu"),
         domains=("fujitsu-general.com", "fglair.com", "airstagecloud.com"), module="airstage"),
    Kind("charger", "an electric car charger", names=("evcp", "syncev", "charger", "wallbox", "zappi", "ohme", "easee"),
         domains=("ampeco.tech", "syncev.co.uk", "myenergi.net", "ohme.io", "easee.cloud", "wall-box.com"), module="ocpp",
         note="World Assistant can be its OCPP server"),
    Kind("ovms", "a car's OVMS module", domains=("openvehicles.com",), names=("ovms",), module="ovms"),
    Kind("inverter", "a solar inverter's Wi-Fi dongle (Sunsynk, Deye, Solis)", vendors=("espressif",), weak_vendor=True,
         domains=("solarmanpv.com", "sunsynk.net", "deyecloud.com", "solarmanpv.cn", "soliscloud.com"),
         note="read through Home Assistant's Sunsynk integration for now (decided 14 November)"),
    Kind("tigo", "a Tigo Cloud Connect Advanced (the optimisers' gateway)", vendors=("tigo energy",), names=("tigo", "cca"),
         domains=("tigoenergy.com",), note="no module yet: an outside module could read it on your network"),
    Kind("home_assistant", "Home Assistant", names=("homeassistant", "home-assistant", "hassio"), domains=("home-assistant.io", "nabu.casa"),
         note="World Assistant already talks to it (Settings → Home Assistant)"),
    Kind("garmin", "a Garmin device", vendors=("garmin",), domains=("garmin.com", "garmin.cn"), module="bikes",
         note="rides reach the bike garage through Garmin Connect"),
    Kind("lg_tv", "an LG TV", vendors=("lg electronics", "lg innotek"), names=("lgwebos", "lg-tv", "webos"),
         domains=("lgtvsdp.com", "lgsmartad.com", "lge.com", "lgappstv.com")),
    Kind("samsung_tv", "a Samsung TV", names=("samsung-tv", "tizen"), domains=("samsungcloudsolution.com", "samsungotn.net", "samsungads.com"),
         vendors=("samsung",), weak_vendor=True),
    Kind("roku", "a Roku", vendors=("roku",), domains=("roku.com",)),
    Kind("sonos", "a Sonos speaker", vendors=("sonos",), domains=("sonos.com",)),
    Kind("echo", "an Amazon Echo or Fire device", vendors=("amazon technologies",), names=("echo", "fire-tv", "firetv", "kindle"),
         domains=("amazon-dss.com", "device-metrics-us.amazon.com", "fireoscaptiveportal.com")),
    Kind("google", "a Google Nest or Chromecast", vendors=("google",), names=("chromecast", "nest", "google-home"),
         domains=("clients3.google.com", "connectivitycheck.gstatic.com"), weak_vendor=True),
    Kind("hue", "a Philips Hue bridge", vendors=("signify", "philips lighting"), domains=("meethue.com",)),
    Kind("printer", "a printer", vendors=("hewlett packard", "brother industries", "seiko epson", "canon"), names=("printer", "hp", "brother", "epson"),
         ports=(631, 9100)),
    Kind("unifi", "a UniFi device", vendors=("ubiquiti",), names=("u6", "u7", "usw", "uap", "udr", "unifi")),
    Kind("nas", "a NAS or server", names=("nas", "unraid", "synology", "truenas"), vendors=("synology",)),
    Kind("phone", "a phone or tablet", names=("iphone", "ipad", "galaxy", "pixel", "android")),
    Kind("computer", "a computer", names=("macbook", "imac", "desktop", "laptop", "pc-")),
    Kind("pihole", "Pi-hole", names=("pihole", "pi-hole")),
)


def vendor_of(mac: str, oui: dict[str, str]) -> str | None:
    """The adapter maker, from the first six hex digits (a locally administered address has none: phones hide theirs)."""
    h = re.sub(r"[^0-9a-f]", "", mac.lower())
    if len(h) < 6:
        return None
    if int(h[1], 16) & 2:
        return "private address (it hides its maker)"
    return oui.get(h[:6].upper())


def _ends(domain: str, ending: str) -> bool:
    return domain == ending or domain.endswith("." + ending)


def identify(device: dict[str, Any], domains: list[str], ports: list[int], oui: dict[str, str]) -> dict[str, Any]:
    """{kind, what, sure (likely | possibly), why, vendor} for one device, or kind None."""
    vendor = vendor_of(device["mac"], oui)
    v = (vendor or "").lower()
    name = " ".join(str(device.get(k) or "") for k in ("name", "alias")).lower()
    best: tuple[int, Kind, list[str]] | None = None
    for k in KINDS:
        why, score = [], 0
        if any(w in v for w in k.vendors):
            why.append(f"its network adapter is made by {vendor}")
            score += 1 if k.weak_vendor else 2
        hits = sorted({e for d in domains for e in k.domains if _ends(d, e)})
        if hits:
            why.append("it looks up " + ", ".join(hits[:3]))
            score += 3
        if any(re.search(rf"(^|[^a-z]){re.escape(w)}", name) for w in k.names):
            why.append(f"the router calls it {device.get('alias') or device.get('name')}")
            score += 2
        if k.ports and any(p in k.ports for p in ports):
            why.append("it uses port " + ", ".join(str(p) for p in sorted(set(k.ports) & set(ports))))
            score += 1
        if score and (best is None or score > best[0]):
            best = (score, k, why)
    if best is None or best[0] < 2:
        return {"kind": None, "what": None, "sure": None, "why": [], "vendor": vendor}
    score, k, why = best
    return {"kind": k.id, "what": k.what, "sure": "likely" if score >= 3 else "possibly", "why": why, "vendor": vendor,
            "module": k.module, "note": k.note}  # fmt: skip


def suggestion(found: dict[str, Any], device: dict[str, Any], enabled: set[str], looked_after: dict[str, set[str]],
               names: dict[str, str], pages: dict[str, str]) -> dict[str, Any] | None:
    """What to do about a device: turn a module on, set it up for this device, nothing (already done), or a note."""
    mod = found.get("module")
    if not mod:
        return {"say": found["note"]} if found.get("note") else None
    label = names.get(mod, mod)
    if mod not in enabled:
        return {"say": f"turn on {label} for it", "module": mod, "link": "/settings#setup", "state": "off"}
    if device.get("ip") in looked_after.get(mod, set()) or device["mac"] in looked_after.get(mod, set()):
        return {"say": f"{label} looks after it", "module": mod, "link": pages.get(mod), "state": "done"}
    addr = f": its address is {device['ip']}" if device.get("ip") else ""
    return {"say": f"set it up in {label}{addr}", "module": mod, "link": pages.get(mod), "state": "set up"}


def oui_rows(csv_text: str) -> list[tuple[str, str]]:
    """The IEEE MA-L register (oui.csv): Registry,Assignment,Organization Name,Organization Address."""
    import csv
    import io

    out = []
    for row in csv.reader(io.StringIO(csv_text)):
        if len(row) >= 3 and re.fullmatch(r"[0-9A-F]{6}", row[1] or ""):
            out.append((row[1], row[2].strip()[:120]))
    return out

"""Advisory firewall-block suggestions for one device, from what the UniFi gateway logged it reaching. Pure.

World Assistant never changes your firewall: it shows what a device talks to and the rule you could add, and you add
it in UniFi yourself. The gateway logs firewall decisions by destination IP and port (not hostname), so this works
from the connection picture — how many outside hosts a device reaches, how often, and on which ports — and names the
tracker-looking domains the device resolves (from Pi-hole's DNS view) so a chatty IoT device is easy to spot.

A device is "chatty" when it reaches an unusual number of outside hosts or opens an unusual number of connections.
Essential ports (DNS, NTP, DHCP) are never suggested for blocking; the web ports (80/443) are flagged as "may break
updates" rather than suggested outright.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

# Domain names that look like they do not serve the person (same sense as the DNS block suggestions).
PURPOSE = re.compile(r"(?:^|[.-])(telemetry|metrics?|analytics|ads?|adservice|advert\w*|beacons?|crash\w*|crashlytics|logs?|logging|"
                     r"track\w*|tracker|stats?|statistics|insights|events?|collect\w*|diagnostic\w*|usage|report\w*|"
                     r"omtrdc|doubleclick|app-measurement|samsungads|lgad|smartclip|tvinteractive)(?:[.-]|$|\d)")  # fmt: skip

ESSENTIAL_PORTS = {53: "DNS", 123: "NTP", 67: "DHCP", 68: "DHCP", 546: "DHCPv6", 547: "DHCPv6"}
MAY_BREAK_PORTS = {80: "HTTP (updates, certificate checks)", 443: "HTTPS (updates, apps, certificate checks)"}
CHATTY_DESTS = 30  # distinct outside hosts in the window
CHATTY_PER_DAY = 500  # firewall-logged connections a day
CLOCK_HOURS = 20  # of 24


def is_private(dest: str | None) -> bool:
    """True for a local address (or anything that is not an IP we can judge): only outside hosts are suggested."""
    if not dest:
        return True
    try:
        ip = ipaddress.ip_address(dest.strip())
    except ValueError:
        return False  # a hostname (rare for firewall logs): treat as outside so it is not hidden
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved


def analyse(rows: list[dict[str, Any]], dns: list[dict[str, Any]], days: int) -> dict[str, Any]:
    """rows: firewall events grouped as {dest, port, proto, action, n, hours}. dns: the device's allowed lookups as
    {dest, n}. Returns the summary, the per-destination suggestions, the tracker domains it resolves, and a rule hint."""
    days = max(days, 1)
    external = [r for r in rows if not is_private(r.get("dest"))]
    distinct = len({r["dest"] for r in external if r.get("dest")})
    per_day = sum(r.get("n", 0) for r in external) / days
    ports = sorted({r["port"] for r in external if r.get("port")})
    blocks_already = sum(r.get("n", 0) for r in rows if r.get("action") == "block")
    chatty = distinct >= CHATTY_DESTS or per_day >= CHATTY_PER_DAY

    allowed = [r for r in external if r.get("action") != "block" and r.get("port") not in ESSENTIAL_PORTS]
    allowed.sort(key=lambda r: -r.get("n", 0))
    suggestions = []
    for r in allowed[:10]:
        why = []
        if r.get("hours", 0) >= CLOCK_HOURS and r.get("n", 0) / days >= 50:
            why.append(f"reached around the clock ({r['n']} connections, {r['hours']} hours of 24)")
        elif r.get("n", 0) / days >= 50:
            why.append(f"{r['n']} connections ({r['n'] / days:.0f} a day)")
        if r.get("port") in MAY_BREAK_PORTS:
            why.append(f"port {r['port']} is usually {MAY_BREAK_PORTS[r['port']]} — blocking may stop updates")
        dest, port = r.get("dest"), r.get("port")
        suggestions.append({"dest": dest, "port": port, "proto": r.get("proto"), "count": r.get("n", 0), "why": why,
                            "rule": f"Block {dest}{':' + str(port) if port else ''}{' ' + r['proto'].upper() if r.get('proto') else ''} from this device"})

    trackers = [x["dest"] for x in dns if x.get("dest") and PURPOSE.search(x["dest"].lower())][:20]
    nonessential = [p for p in ports if p not in ESSENTIAL_PORTS and p not in MAY_BREAK_PORTS]
    rule_hint = None
    if chatty:
        extra = f"; it also uses ports {', '.join(map(str, nonessential[:8]))}" if nonessential else ""
        rule_hint = (f"This device reached {distinct} outside hosts ({per_day:.0f} connections a day). If it only needs "
                     f"local control, add a firewall rule blocking its internet access except DNS (53) and NTP (123){extra}.")
    return {"summary": {"distinct_dests": distinct, "per_day": round(per_day), "ports": ports,
                        "blocks_already": blocks_already, "chatty": chatty},
            "suggestions": suggestions, "tracker_domains": trackers, "rule_hint": rule_hint}

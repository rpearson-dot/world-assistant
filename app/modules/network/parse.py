"""Reading what the UniFi gateway sends by syslog, and what Pi-hole's API answers. Pure.

The gateway sends three kinds of line to one port:
- **System events** in CEF (`CEF:0|Ubiquiti|UniFi Network|...`): a client joining or leaving, with its MAC address,
  IP address, name and the access point or port. The field names vary between versions, so any field naming a MAC,
  IP or host name is read.
- **Firewall decisions** in iptables form (`[RULE] IN=br0 OUT=eth4 MAC=... SRC=... DST=... PROTO=TCP SPT=... DPT=...`),
  for each rule with syslog logging on. Allowed or blocked comes from the rule's name (A/D/R in the generated names,
  or a description starting Allow or Block).
- **DHCP** from dnsmasq (`DHCPACK(br0) 10.0.2.50 aa:bb:cc:dd:ee:ff phone`): which address and name a device was given.

Anything else is kept as a system line with its text.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

MAC = re.compile(r"(?i)\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b")
PRI = re.compile(r"^<\d{1,3}>")
RFC5424 = re.compile(r"^1 (\S+) (\S+) (\S+) (\S+) (\S+) (-|\[.*?\]) ?(.*)$")
RFC3164 = re.compile(r"^([A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d) (\S+) (.*)$")
DHCP = re.compile(r"DHCP(ACK|OFFER|REQUEST|RELEASE|DISCOVER|INFORM)\(\S+\)\s+(\d+\.\d+\.\d+\.\d+)?\s*([0-9a-fA-F:]{17})\s*(\S+)?")
IPT = re.compile(r"\b(IN|OUT|MAC|SRC|DST|PROTO|SPT|DPT|LEN)=(\S*)")
RULE = re.compile(r"\[([^\]]+)\]")
BLOCKED = frozenset({"GRAVITY", "REGEX", "DENYLIST", "BLACKLIST", "EXTERNAL_BLOCKED_IP", "EXTERNAL_BLOCKED_NULL",
                     "EXTERNAL_BLOCKED_NXRA", "GRAVITY_CNAME", "REGEX_CNAME", "DENYLIST_CNAME", "BLACKLIST_CNAME",
                     "SPECIAL_DOMAIN", "DBBUSY"})  # fmt: skip


def mac(v: Any) -> str | None:
    m = MAC.search(str(v or ""))
    return m[1].lower() if m else None


def strip_header(line: str) -> tuple[str, str | None]:
    """The message without its syslog header, and the sending program's name when there is one."""
    s = PRI.sub("", line.strip(), count=1)
    m = RFC5424.match(s)
    if m:
        return m[7].lstrip("\ufeff"), m[3]
    m = RFC3164.match(s)
    if m:
        rest = m[3]
        tag = re.match(r"([\w./-]+)(?:\[\d+\])?:\s?(.*)$", rest)
        return (tag[2], tag[1]) if tag else (rest, None)
    return s, None


def cef(msg: str) -> dict[str, Any] | None:
    """CEF:Version|Vendor|Product|Version|SignatureID|Name|Severity|Extension (key=value, \\= and \\| escaped)."""
    i = msg.find("CEF:")
    if i < 0:
        return None
    parts, cur, esc = [], "", False
    for ch in msg[i + 4:]:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == "|" and len(parts) < 7:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    if len(parts) < 8:
        return None
    ext: dict[str, str] = {}
    for m in re.finditer(r"(\w+)=((?:\\=|[^=])*?)(?=\s+\w+=|$)", parts[7]):
        ext[m[1]] = m[2].replace("\\=", "=").strip()
    return {"vendor": parts[1], "product": parts[2], "signature": parts[4], "name": parts[5], "severity": parts[6], "ext": ext}


def _field(ext: dict[str, str], *words: str) -> str | None:
    for k, v in ext.items():
        lk = k.lower()
        if any(w in lk for w in words) and v:
            return v
    return None


def line(raw: str, now: datetime | None = None) -> dict[str, Any]:
    """One syslog line as an event: {at, source, kind, mac, ip, name, action, dest, port, proto, detail}."""
    at = now or datetime.now(UTC)
    msg, prog = strip_header(raw)
    ev: dict[str, Any] = {"at": at, "source": "unifi", "kind": "system", "mac": None, "ip": None, "name": None,
                          "action": None, "dest": None, "port": None, "proto": None, "detail": {}}  # fmt: skip
    c = cef(msg)
    if c:
        ext = c["ext"]
        words = c["name"].lower()
        ev.update(kind="client" if any(w in words for w in ("client", "connect", "join", "roam", "disconnect", "left", "station"))
                  else "system", mac=mac(_field(ext, "mac")), ip=_field(ext, "clientip", "ip", "src"),
                  name=_field(ext, "hostname", "alias", "clientname", "name"),
                  action="leave" if any(w in words for w in ("disconnect", "left", "offline")) else
                  "join" if any(w in words for w in ("connect", "join", "online")) else "roam" if "roam" in words else None,
                  detail={"event": c["name"], "ap": _field(ext, "apname", "ap", "device", "port"), "ssid": _field(ext, "ssid", "wlan", "wifi"),
                          "severity": c["severity"]})  # fmt: skip
        if ev["ip"] and not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ev["ip"]):
            ev["ip"] = None
        return ev
    d = DHCP.search(msg)
    if d and (prog or "").startswith("dnsmasq") or (d and "DHCP" in msg):
        ev.update(kind="dhcp", action="lease" if d[1] == "ACK" else d[1].lower(), ip=d[2], mac=d[3].lower(),
                  name=d[4] if d[4] and d[4] != "*" else None)  # fmt: skip
        return ev
    f = dict(IPT.findall(msg))
    if "SRC" in f and "DST" in f:
        rule = (RULE.search(msg) or [None, ""])[1] or ""
        r = rule.upper()
        blocked = bool(re.search(r"-(D|R)-|\bBLOCK|DROP|REJECT|DENY", r))
        allowed = bool(re.search(r"-A-|\bALLOW|ACCEPT", r))
        # the MAC= field is destination MAC, source MAC, type: the sender's is the second
        macs = [x for x in (f.get("MAC") or "").split(":")]
        src_mac = ":".join(macs[6:12]).lower() if len(macs) >= 12 else None
        ev.update(kind="firewall", ip=f["SRC"], mac=src_mac, dest=f["DST"], proto=(f.get("PROTO") or "").lower() or None,
                  port=int(f["DPT"]) if (f.get("DPT") or "").isdigit() else None,
                  action="block" if blocked else "allow" if allowed else "logged",
                  detail={"rule": rule, "in": f.get("IN"), "out": f.get("OUT"), "sport": f.get("SPT")})  # fmt: skip
        return ev
    ev["detail"] = {"program": prog, "text": msg[:400]}
    return ev


def pihole(q: dict[str, Any]) -> dict[str, Any]:
    """One query from Pi-hole v6's /api/queries as an event."""
    client = q.get("client") or {}
    status = str(q.get("status") or "").upper()
    when = q.get("time")
    return {"at": datetime.fromtimestamp(float(when), UTC) if when else datetime.now(UTC), "source": "pihole", "kind": "dns",
            "mac": None, "ip": client.get("ip"), "name": client.get("name"), "action": "block" if status in BLOCKED else "allow",
            "dest": (q.get("domain") or "")[:253] or None, "port": None, "proto": str(q.get("type") or "").lower() or None,
            "detail": {"status": status, "id": q.get("id")}}  # fmt: skip

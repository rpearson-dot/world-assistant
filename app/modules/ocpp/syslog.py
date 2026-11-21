"""Reading one syslog line from the charger. Pure, and self-contained (no other module): ordinary syslog, so it
belongs to no feature.

The charger speaks ordinary syslog: an optional priority `<NN>` (facility and severity), then a header World Assistant
does not need, then the message. We keep the raw line as it came, and a tidied message and severity to read and sort by.
What the charger puts in that message is its own; we do not assume a format, so this works whatever firmware it runs.
"""

from __future__ import annotations

import re
from typing import Any

_PRI = re.compile(r"^<(\d{1,3})>")
_RFC5424 = re.compile(r"^1 \S+ (\S+) \S+ \S+ \S+ (?:-|\[.*?\]) ?(.*)$")
_RFC3164 = re.compile(r"^([A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d) (\S+) (.*)$")
_TAG = re.compile(r"[\w./-]+(?:\[\d+\])?:\s?(.*)$")
# syslog severities 0..7, most to least urgent
SEVERITY = ("emergency", "alert", "critical", "error", "warning", "notice", "info", "debug")


def _strip_header(raw: str) -> str:
    """The message with its syslog priority and header removed (RFC 5424 or 3164); the whole line if neither fits."""
    s = _PRI.sub("", raw.strip(), count=1)
    m = _RFC5424.match(s)
    if m:
        return m[2].lstrip("\ufeff")
    m = _RFC3164.match(s)
    if m:
        rest = m[3]
        tag = _TAG.match(rest)
        return tag[1] if tag else rest
    return s


def read(raw: str) -> dict[str, Any]:
    """One charger syslog line as {facility, severity, message, raw}. raw is kept verbatim (trimmed of trailing space)."""
    raw = raw.rstrip()
    facility: int | None = None
    severity: int | None = None
    m = _PRI.match(raw.lstrip())
    if m:
        pri = int(m[1])
        if pri <= 191:  # 23 facilities * 8 severities - 1
            facility, severity = pri >> 3, pri & 7
    message = _strip_header(raw)
    return {"facility": facility, "severity": severity, "message": message[:1000] or raw[:1000], "raw": raw[:2000]}


def severity_word(severity: int | None) -> str:
    return SEVERITY[severity] if severity is not None and 0 <= severity < len(SEVERITY) else ""


_H5424 = re.compile(r"^1 \S+ (\S+) ")
_H3164 = re.compile(r"^[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d (\S+) ")


def hostname(raw: str) -> str | None:
    """The host the syslog line names, if any (RFC 5424 or 3164), lower-cased. Used to spot a charger whose address
    has changed: chargers commonly send their charge-point ID as the host. '-' (RFC 5424 nil) counts as absent."""
    s = _PRI.sub("", raw.strip(), count=1)
    m = _H5424.match(s) or _H3164.match(s)
    h = m[1] if m else None
    return h.lower() if h and h != "-" else None

"""Suggested blocks for each device: what gets past your Pi-hole lists that does not serve you. Pure.

Only lookups Pi-hole let through count (what your lists already block is not suggested again). A domain is
suggested when:
- a manufacturer's tracker list names it (open lists, used only to recognise it: never added to Pi-hole wholesale);
- its name says what it is for (telemetry, metrics, analytics, ads, beacon, crash reports, logging);
- or the device looks it up around the clock, whether or not anyone is using it.

Never suggested: what a device needs to work and stay safe (time, updates, certificate checks, the checks for a Wi-Fi
sign-in page). Those are shown as "keep". Where blocking something is known to break a feature, the suggestion says
so.
"""

from __future__ import annotations

import re
from typing import Any

PURPOSE = re.compile(r"(?:^|[.-])(telemetry|metrics?|analytics|ads?|adservice|advert\w*|beacons?|crash\w*|crashlytics|logs?|logging|"
                     r"track\w*|tracker|stats?|statistics|insights|events?|collect\w*|diagnostic\w*|usage|report\w*|"
                     r"omtrdc|doubleclick|app-measurement|samsungads|lgad|smartclip|tvinteractive)(?:[.-]|$|\d)")  # fmt: skip
KEEP = re.compile(r"(?:^|\.)(ntp|time|pool\.ntp|time\.apple|time\.windows|ocsp|crl|pki|certificates?|update|updates|firmware|fota|"
                  r"swupdate|download\.windowsupdate|connectivitycheck|captive|msftconnecttest|msftncsi|"
                  r"clients3\.google|generate_204|detectportal|nmcheck)(?:[.-]|$)")  # fmt: skip
BREAKS = {  # what may stop working: by a word in the domain
    "lgtvsdp": "the LG TV's app store and some apps", "samsungcloud": "Samsung account and backup",
    "netflix": "Netflix", "youtube": "YouTube", "googlevideo": "YouTube", "cast": "casting to it",
    "amazonvideo": "Prime Video", "roku": "the Roku channel store", "alexa": "Alexa",
}
AROUND_THE_CLOCK = 20  # hours of the day (of 24) it was looked up in
DAILY = 50  # lookups a day, for "around the clock" to count


def base(domain: str) -> str:
    """The registrable part, roughly: the last two labels, or three under a two-part ending (co.uk)."""
    p = domain.lower().strip(".").split(".")
    if len(p) >= 3 and p[-2] in ("co", "com", "org", "net", "gov", "ac", "ne", "or") and len(p[-1]) == 2:
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def reasons(domain: str, stats: dict[str, Any], lists: dict[str, list[str]]) -> tuple[str, list[str]]:
    """("suggest" | "keep" | "fine", why) for one domain one device looked up and Pi-hole let through."""
    d = domain.lower()
    if KEEP.search(d):
        return "keep", ["needed to work or stay safe (time, updates, certificates or the Wi-Fi sign-in check)"]
    why = []
    named = lists.get(d) or lists.get(base(d))
    if named:
        why.append("on " + " and ".join(sorted(set(named))) + "'s tracker list")
    m = PURPOSE.search(d)
    if m:
        why.append(f"named for {m[1].lower()}")
    if stats.get("hours", 0) >= AROUND_THE_CLOCK and stats.get("per_day", 0) >= DAILY:
        why.append(f"looked up around the clock ({stats['per_day']:.0f} times a day, in {stats['hours']} hours of 24)")
    return ("suggest", why) if why else ("fine", [])


def breaks(domain: str) -> str | None:
    d = domain.lower()
    return next((v for k, v in BREAKS.items() if k in d), None)


def for_device(rows: list[dict[str, Any]], lists: dict[str, list[str]], days: float, blocked_here: set[str]) -> dict[str, Any]:
    """A device's suggestions from its allowed lookups: rows {domain, n, hours} (hours: distinct hours of the day).
    Already blocked through World Assistant: shown as such, not suggested again."""
    sug, keep = [], []
    for r in sorted(rows, key=lambda x: -x["n"]):
        stats = {"per_day": r["n"] / max(days, 1), "hours": r["hours"]}
        verdict, why = reasons(r["domain"], stats, lists)
        item = {"domain": r["domain"], "lookups": r["n"], "per_day": round(stats["per_day"], 1), "why": why}
        if verdict == "keep":
            keep.append(item)
        elif verdict == "suggest" and r["domain"] not in blocked_here:
            if b := breaks(r["domain"]):
                item["may_break"] = b
            sug.append(item)
    return {"suggested": sug[:100], "keep": keep[:30], "suggested_lookups": sum(x["lookups"] for x in sug)}

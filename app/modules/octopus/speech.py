"""The Energy answer as a sentence for the assistant (voice, Siri, MCP). Pure."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


def _t(iso: str, tz: ZoneInfo, now: datetime) -> str:
    d = datetime.fromisoformat(iso).astimezone(tz)
    clock = d.strftime("%H:%M")
    n = now.astimezone(tz)
    if d.date() == n.date():
        return clock
    return f"{d.strftime('%A')} {clock}"


def sentence(now_: dict[str, Any], tz: ZoneInfo, now: datetime) -> str:
    cur = now_["now"]
    parts = [
        f"Electricity is {cur['band']} now at {cur['pence']:.1f}p, until {_t(cur['until'], tz, now)}"
    ]
    nxt = now_.get("next")
    if nxt:
        parts.append(f"then {nxt['band']} at {nxt['pence']:.1f}p")
    text = ", ".join(parts) + "."
    cheap = now_.get("next_cheap")
    if cheap and not now_.get("cheap_now"):
        text += f" The next cheap rate is {cheap['pence']:.1f}p from {_t(cheap['start'], tz, now)} to {_t(cheap['end'], tz, now)}."
    peak = now_.get("next_peak")
    if peak and cur["band"] != "peak":
        text += f" Peak is {peak['pence']:.1f}p from {_t(peak['start'], tz, now)} to {_t(peak['end'], tz, now)}."
    block = now_.get("cheapest_block")
    if block:
        text += f" The cheapest {block['requested_hours']:g} hours start at {_t(block['start'], tz, now)}, averaging {block['pence']:.1f}p."
    saving = (now_.get("sessions") or {}).get("saving") or {}
    if saving.get("now"):
        joined = (
            "and you have joined it" if saving.get("now_joined") else "and you have not joined it"
        )
        text += f" A Saving Session is on now {joined}."
    elif saving.get("next_joined"):
        text += f" Your next joined Saving Session starts {_t(saving['next_joined']['start'], tz, now)}."
    elif saving.get("next"):
        text += f" The next Saving Session starts {_t(saving['next']['start'], tz, now)}; you have not joined it."
    free = (now_.get("sessions") or {}).get("free") or {}
    if free.get("now_joined"):
        text += f" Electricity is free now, until {_t(free['current']['end'], tz, now)}; you joined this one."
    elif free.get("next_joined"):
        n = free["next_joined"]
        text += f" Your next free electricity ({n['type']}) is {_t(n['start'], tz, now)} to {_t(n['end'], tz, now)}; you joined it."
    elif free.get("next"):
        n = free["next"]
        text += (f" Free electricity ({n['type']}) is next on from {_t(n['start'], tz, now)} to {_t(n['end'], tz, now)}; "
                 "you have not joined it.")  # fmt: skip
    return text

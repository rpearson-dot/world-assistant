"""When the recorder runs: the window (07:00 to 09:00 by default) on school days, or on every day while testing, or
now for a while when asked. Pure, apart from the bank holidays it is given.

A school day is a weekday inside a term, outside its half term, holidays and INSET days, and not a bank holiday. The
calendar starts as Coombe Girls School's 2026/27 dates and is edited on the page.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

DEFAULT_CALENDAR: dict[str, Any] = {
    "school": "Coombe Girls School",
    "terms": [
        {"name": "Autumn term", "from": "2026-09-07", "to": "2026-12-18"},
        {"name": "Spring term", "from": "2027-01-05", "to": "2027-03-25"},
        {"name": "Summer term", "from": "2027-04-12", "to": "2027-07-23"},
    ],
    "breaks": [
        {"name": "Autumn half term", "from": "2026-10-26", "to": "2026-11-03"},
        {"name": "Spring half term", "from": "2027-02-15", "to": "2027-02-19"},
        {"name": "Summer half term", "from": "2027-05-31", "to": "2027-06-04"},
    ],
    "inset": ["2026-09-03", "2026-09-04", "2026-11-04", "2027-01-04", "2027-07-02"],
    "notes": [
        {"date": "2026-09-07", "note": "Years 7 and 12 only"},
        {"date": "2027-04-12", "note": "School starts at 10:45 (gates open 10:30)"},
    ],
}

DEFAULT_SETTINGS: dict[str, Any] = {"line": "213", "start": "07:00", "end": "09:00", "every_day": False}


def _d(v: str) -> date:
    return date.fromisoformat(v)


def school_day(day: date, cal: dict[str, Any], bank_holiday: bool = False) -> tuple[bool, str]:
    """Is it a school day, and why (or why not)."""
    if day.weekday() >= 5:
        return False, "weekend"
    if bank_holiday:
        return False, "bank holiday"
    if day.isoformat() in set(cal.get("inset") or []):
        return False, "INSET day"
    for b in cal.get("breaks") or []:
        if _d(b["from"]) <= day <= _d(b["to"]):
            return False, b.get("name") or "school holiday"
    for t in cal.get("terms") or []:
        if _d(t["from"]) <= day <= _d(t["to"]):
            note = next((n["note"] for n in cal.get("notes") or [] if n.get("date") == day.isoformat()), None)
            return True, (t.get("name") or "term") + (f" ({note})" if note else "")
    return False, "outside term"


def _hm(v: str) -> time:
    h, m = v.split(":")
    return time(int(h), int(m))


def in_window(now: datetime, st: dict[str, Any]) -> bool:
    return _hm(st["start"]) <= now.time().replace(second=0, microsecond=0) < _hm(st["end"])


def recording(now: datetime, st: dict[str, Any], cal: dict[str, Any], bank_holiday: bool, until: float | None = None) -> tuple[bool, str]:
    """Should the recorder be asking now (local time), and why."""
    if until and now.timestamp() < until:
        return True, "recording now, as asked"
    if not in_window(now, st):
        return False, f"outside {st['start']} to {st['end']}"
    if st.get("every_day"):
        return True, "every day (test)"
    ok, why = school_day(now.date(), cal, bank_holiday)
    return ok, why


def check_calendar(cal: dict[str, Any]) -> dict[str, Any]:
    """A calendar as saved: dates checked, ranges the right way round. Raises ValueError with the first problem."""
    out: dict[str, Any] = {"school": str(cal.get("school") or "")[:80], "terms": [], "breaks": [], "inset": [], "notes": []}
    for kind in ("terms", "breaks"):
        for r in cal.get(kind) or []:
            a, b = _d(str(r["from"])), _d(str(r["to"]))
            if b < a:
                raise ValueError(f"{r.get('name') or kind}: ends before it starts")
            out[kind].append({"name": str(r.get("name") or "")[:60], "from": a.isoformat(), "to": b.isoformat()})
    out["inset"] = sorted({_d(str(x)).isoformat() for x in cal.get("inset") or []})
    out["notes"] = [{"date": _d(str(n["date"])).isoformat(), "note": str(n.get("note") or "")[:120]} for n in cal.get("notes") or []]
    return out


def drop_past(cal: dict[str, Any], today: date) -> dict[str, Any]:
    """The calendar without what is over: terms and holidays that ended before today, INSET days and notes before
    today. Only today and later are ever asked about, so nothing is lost."""
    out = dict(cal)
    for kind in ("terms", "breaks"):
        out[kind] = [r for r in cal.get(kind) or [] if _d(str(r["to"])) >= today]
    out["inset"] = [x for x in cal.get("inset") or [] if _d(str(x)) >= today]
    out["notes"] = [n for n in cal.get("notes") or [] if _d(str(n["date"])) >= today]
    return out


def check_settings(st: dict[str, Any]) -> dict[str, Any]:
    out = {**DEFAULT_SETTINGS, **{k: v for k, v in st.items() if k in DEFAULT_SETTINGS}}
    line = str(out["line"]).strip().upper()
    if not line or len(line) > 8 or not line.replace("-", "").isalnum():
        raise ValueError("a route number such as 213")
    if _hm(out["end"]) <= _hm(out["start"]):
        raise ValueError("the window must end after it starts")
    return {"line": line, "start": _hm(out["start"]).strftime("%H:%M"), "end": _hm(out["end"]).strftime("%H:%M"),
            "every_day": bool(out["every_day"])}  # fmt: skip

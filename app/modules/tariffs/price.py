"""Pricing your real consumption at each tariff's rates today. Pure.

A tariff's rates today are a **day pattern**: the unit rate for each half-hour of the local day (48 slots), plus its
standing charge a day. Flat tariffs have one rate; Go, Cosy, Flux and the like a rate per band; Agile and Tracker,
whose rates change every day, the average of each half-hour over the last 30 days (marked "averaged"). Your readings
(each half-hour's kWh bought, and sold) are priced slot by slot, so the comparison is your use, today's prices, and
nothing from past price levels.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

SLOTS = 48


def slot(at: datetime, tz: Any) -> int:
    local = at.astimezone(tz)
    return local.hour * 2 + (1 if local.minute >= 30 else 0)


def in_force(rows: list[dict[str, Any]], t: datetime) -> float | None:
    """The rate in force at t (the newest row starting at or before it that has not ended)."""
    got = None
    for r in rows:
        if r["valid_from"] > t:
            break
        if r.get("valid_to") is None or t < r["valid_to"]:
            got = float(r["pence_inc_vat"])
    return got


def pattern(rates: list[dict[str, Any]], tz: Any, now: datetime, average_days: int = 30) -> tuple[list[float] | None, bool]:
    """The day pattern from a tariff's unit-rate rows (valid_from, valid_to or None, pence_inc_vat).

    Rates in force now are read for each half-hour of the coming day where the tariff publishes them that far (a
    fixed or banded tariff: open-ended rows, or a daily repeating pattern); a tariff whose rows change day to day
    (more than three different rates for the same half-hour across the last `average_days`) is averaged per slot over
    those days instead. Returns (48 rates, averaged) or (None, False) when the rows say nothing for today."""
    if not rates:
        return None, False
    rows = sorted(rates, key=lambda r: r["valid_from"])
    per: dict[int, list[float]] = defaultdict(list)
    t = (now - timedelta(days=average_days)).replace(minute=0, second=0, microsecond=0)
    while t < now:
        p = in_force(rows, t)
        if p is not None:
            per[slot(t, tz)].append(p)
        t += timedelta(minutes=30)
    dynamic = any(len({round(v, 2) for v in vs}) > 3 for vs in per.values())
    if dynamic:
        if len(per) < SLOTS:
            return None, True
        return [round(sum(per[i]) / len(per[i]), 3) for i in range(SLOTS)], True
    # not dynamic: what is in force for each slot over the next day (today's rates, not the past's)
    out: list[float | None] = [None] * SLOTS
    t = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
    for _ in range(SLOTS):
        out[slot(t, tz)] = in_force(rows, t)
        t += timedelta(minutes=30)
    if any(v is None for v in out):  # the next day not published yet: the last day that was
        for i in range(SLOTS):
            if out[i] is None and per.get(i):
                out[i] = per[i][-1]
    if any(v is None for v in out):
        return None, False
    return [round(v, 3) for v in out], False  # type: ignore[arg-type]


def bands(rows: list[dict[str, Any]]) -> list[float]:
    """A day pattern from bands a person typed: [{from: "00:30", to: "05:30", pence: 7.5}, ...]. A band with from
    equal to to, or "00:00" to "24:00", is all day; the first band covering a slot wins; slots no band covers take
    the first band's rate."""
    def mins(s: str) -> int:
        h, m = (s or "0:0").split(":")
        return int(h) * 60 + int(m)

    out: list[float | None] = [None] * SLOTS
    for b in rows:
        a, z = mins(b["from"]), mins(b["to"])
        z = a + 1440 if z == a else z if z > a else z + 1440
        for i in range(SLOTS):
            m = i * 30
            if out[i] is None and (a <= m < z or a <= m + 1440 < z):
                out[i] = float(b["pence"])
    first = float(rows[0]["pence"]) if rows else 0.0
    return [first if v is None else v for v in out]


def cost(readings: list[dict[str, Any]], pat: list[float], tz: Any) -> dict[str, Any]:
    """kWh and pence for half-hour readings (start, kwh) at a day pattern."""
    kwh = pence = 0.0
    for r in readings:
        kwh += r["kwh"]
        pence += r["kwh"] * pat[slot(r["start"], tz)]
    return {"kwh": round(kwh, 1), "pence": round(pence, 1)}


def cheap_slots(pat: list[float]) -> list[bool]:
    """The tariff's cheap half-hours: within a quarter of the way from its lowest rate to its highest. A flat tariff
    has none (nothing to move use into)."""
    lo, hi = min(pat), max(pat)
    if hi - lo < 0.5:
        return [False] * SLOTS
    return [v <= lo + 0.25 * (hi - lo) + 1e-9 for v in pat]


def windows(pat: list[float]) -> dict[str, Any]:
    """The cheap windows of a day: how many, the hours in all, each window, and the longest gap between them (round
    the clock), which is what a house battery has to bridge."""
    cheap = cheap_slots(pat)
    if not any(cheap):
        return {"count": 0, "hours": 0.0, "windows": [], "longest_gap_hours": 24.0}
    first = next(i for i in range(SLOTS) if not cheap[i - 1] and cheap[i]) if not all(cheap) else 0
    runs, i = [], 0
    while i < SLOTS:
        j = (first + i) % SLOTS
        if cheap[j]:
            n = 0
            while n < SLOTS and cheap[(j + n) % SLOTS]:
                n += 1
            runs.append((j, n))
            i += n
        else:
            i += 1
    hm = lambda k: f"{(k % SLOTS) // 2:02d}:{(k % 2) * 30:02d}"
    gaps = [((runs[(k + 1) % len(runs)][0] - (a + n)) % SLOTS) / 2 for k, (a, n) in enumerate(runs)]
    return {"count": len(runs), "hours": sum(n for _, n in runs) / 2,
            "windows": [f"{hm(a)} to {hm(a + n)}" for a, n in sorted(runs)],
            "longest_gap_hours": max(gaps) if gaps else 0.0, "cheap_p": round(min(pat), 2)}  # fmt: skip


def shifted(readings: list[dict[str, Any]], pat: list[float], tz: Any, capacity_kwh: float, charge_kw: float,
            efficiency: float = 0.9) -> dict[str, Any]:
    """kWh and pence if a house battery filled in every cheap window (at up to `charge_kw`, as often as the tariff has
    windows) and covered use outside them until empty. Readings are taken as the house's need, half-hour by
    half-hour; `efficiency` is what comes back out of what goes in."""
    cheap, step, soc = cheap_slots(pat), charge_kw / 2, 0.0
    kwh = pence = 0.0
    for r in sorted(readings, key=lambda x: x["start"]):
        s, d = slot(r["start"], tz), r["kwh"]
        if cheap[s]:
            put = min(step, (capacity_kwh - soc) / efficiency)
            soc += put * efficiency
            buy = d + put
        else:
            use = min(d, soc, step)
            soc -= use
            buy = d - use
        kwh += buy
        pence += buy * pat[s]
    return {"kwh": round(kwh, 1), "pence": round(pence, 1)}


def span_days(readings: list[dict[str, Any]], tz: Any) -> int:
    return len({r["start"].astimezone(tz).date() for r in readings})


def compare(imports: list[dict[str, Any]], exports: list[dict[str, Any]], tariffs: list[dict[str, Any]], tz: Any,
            current: str | None = None, battery: dict[str, float] | None = None) -> dict[str, Any]:
    """Each tariff's cost over the readings and a year at that rate, ranked; import and export apart.

    A tariff: {code, name, supplier, direction (import or export), pattern (48 rates), standing_p (a day; import),
    averaged, conditions: [str], exit_fee_p, pairs_with: family or None}. With `battery` ({capacity_kwh, charge_kw}),
    each import tariff is also priced with the battery moving use into its cheap windows, and that cost ranks."""
    days = max(1, span_days(imports or exports, tz))
    scale = 365 / days
    rows = []
    for t in tariffs:
        if not t.get("pattern"):
            continue
        is_import = t["direction"] == "import"
        c = cost(imports if is_import else exports, t["pattern"], tz)
        standing = (t.get("standing_p") or 0.0) * days if is_import else 0.0
        total = c["pence"] + standing if is_import else -c["pence"]  # what export earns, as a negative cost
        moved = None
        if is_import and battery and battery.get("capacity_kwh") and battery.get("charge_kw"):
            sh = shifted(imports, t["pattern"], tz, float(battery["capacity_kwh"]), float(battery["charge_kw"]))
            moved = sh["pence"] + standing
        rows.append({**{k: t.get(k) for k in ("code", "name", "supplier", "direction", "averaged", "conditions",
                                               "exit_fee_p", "pairs_with", "source")},
                     "kwh": c["kwh"], "units_pounds": round(c["pence"] / 100, 2), "standing_pounds": round(standing / 100, 2),
                     "period_pounds": round(total / 100, 2), "year_pounds": round(total * scale / 100, 0),
                     "average_p": round(c["pence"] / c["kwh"], 2) if c["kwh"] else None,
                     "standing_p": t.get("standing_p"), "current": t.get("code") == current,
                     "windows": windows(t["pattern"]) if is_import else None,
                     "shifted_period_pounds": None if moved is None else round(moved / 100, 2),
                     "shifted_year_pounds": None if moved is None else round(moved * scale / 100, 0),
                     "rank_year_pounds": round((total if moved is None else min(total, moved)) * scale / 100, 0)})  # fmt: skip
    imp = sorted((r for r in rows if r["direction"] == "import"), key=lambda r: r["rank_year_pounds"])
    exp = sorted((r for r in rows if r["direction"] == "export"), key=lambda r: r["rank_year_pounds"])
    return {"days": days, "full_year": days >= 360, "import": imp, "export": exp, "battery": battery or None,
            "suggestion": suggest(imp, exp, current)}  # fmt: skip


def _fits(imp: dict[str, Any], exp: dict[str, Any] | None) -> bool:
    """A paired tariff (Flux, Intelligent Flux) comes with its own export and no other; other exports go with any
    import that is not paired."""
    fam_i, fam_e = imp.get("pairs_with"), (exp or {}).get("pairs_with")
    if fam_i or fam_e:
        return fam_i == fam_e
    return True


def suggest(imp: list[dict[str, Any]], exp: list[dict[str, Any]], current: str | None) -> dict[str, Any] | None:
    """The cheapest import and export together (a year), against what you are on now."""
    combos = []
    for i in imp:
        fits = [e for e in exp if _fits(i, e)]
        if not fits and i.get("pairs_with"):
            continue  # a paired import without its export priced: cannot judge
        e = fits[0] if fits else None
        combos.append((i["rank_year_pounds"] + (e["rank_year_pounds"] if e else 0.0), i, e))
    if not combos:
        return None
    combos.sort(key=lambda c: c[0])
    best = combos[0]
    now_i = next((i for i in imp if i["current"]), None)
    now_c = next((c for c in combos if c[1] is now_i), None) if now_i else None
    out = {"import": best[1]["code"], "import_name": best[1]["name"], "export": best[2]["code"] if best[2] else None,
           "export_name": best[2]["name"] if best[2] else None, "year_pounds": round(best[0])}  # fmt: skip
    if now_c:
        out |= {"now_year_pounds": round(now_c[0]), "saving_pounds": round(now_c[0] - best[0]),
                "already_best": now_c[1] is best[1]}  # fmt: skip
    return out

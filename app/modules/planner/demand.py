"""How predictable the house's evening demand is from the weather. Pure: given each day's evening kWh (16:00 to
19:00 by default) and the same evening's heating degree-hours, a straight-line fit, how good it is, and what it says
for a coming evening (the likely figure, and the figure 9 evenings in 10 stay under).

R² says how much of the day-to-day difference the cold explains: 0.6 and above is predictable enough to act on, 0.3
to 0.6 partly, below that not (other things, cooking, guests, dominate).
"""

from __future__ import annotations

from math import sqrt
from typing import Any

Z90 = 1.2816  # one-sided: 9 in 10 below


def fit(points: list[tuple[float, float]]) -> dict[str, Any]:
    """Least squares y = base + per_dh · x over (degree-hours, kWh) points."""
    n = len(points)
    if n < 10:
        return {"days": n, "enough": False, "why": f"{n} days with both weather and demand: at least 10 are needed"}
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in points)
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx if sxx else 0.0
    base = my - slope * mx
    resid = [y - (base + slope * x) for x, y in points]
    sse = sum(r * r for r in resid)
    r2 = 1 - sse / syy if syy else 0.0
    sd = sqrt(sse / (n - 2)) if n > 2 else 0.0
    verdict = "predictable" if r2 >= 0.6 else "partly predictable" if r2 >= 0.3 else "not predictable"
    return {"days": n, "enough": True, "base_kwh": round(base, 2), "kwh_per_degree_hour": round(slope, 3),
            "r2": round(r2, 2), "spread_kwh": round(sd, 2), "verdict": verdict,
            "mean_kwh": round(my, 2), "mean_degree_hours": round(mx, 1)}  # fmt: skip


def predict(f: dict[str, Any], degree_hours: float) -> dict[str, Any] | None:
    if not f.get("enough"):
        return None
    likely = f["base_kwh"] + f["kwh_per_degree_hour"] * degree_hours
    return {"degree_hours": round(degree_hours, 1), "likely_kwh": round(max(0.0, likely), 2),
            "nine_in_ten_kwh": round(max(0.0, likely + Z90 * f["spread_kwh"]), 2),
            "at_least_kwh": round(max(0.0, likely - Z90 * f["spread_kwh"]), 2)}  # fmt: skip


def evenings(hourly: list[tuple[Any, float]], tz: Any, start_h: int = 16, end_h: int = 19) -> dict[str, float]:
    """kWh in the evening window of each local day, from hourly kWh. Only whole windows count."""
    sums: dict[str, list[float]] = {}
    for at, kwh in hourly:
        local = at.astimezone(tz)
        if start_h <= local.hour < end_h:
            sums.setdefault(local.date().isoformat(), []).append(kwh)
    return {d: round(sum(v), 3) for d, v in sums.items() if len(v) == end_h - start_h}

"""The routes you ride again and again, found from your rides' GPS routes. Pure.

Two rides are the same route when they start and finish within 400 m of each other (either way round) and every
part of each runs within 250 m of the other on average. Only rides from the last few years count (5 by default:
older ones show roads you no longer ride), and only routes ridden more than a number of times (5 by default) are
offered. A route can then be watched like a trip: its corridor is kept, and road events along it found.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from datetime import datetime, timedelta
from typing import Any

NEAR_ENDS_M, NEAR_LINE_M, SAMPLES = 400.0, 250.0, 30


def _m(a: list[float], b: list[float]) -> float:
    dy = (b[0] - a[0]) * 111_320
    dx = (b[1] - a[1]) * 111_320 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def resample(route: list[list[float]], n: int = SAMPLES) -> list[list[float]]:
    """n points evenly along the route by distance."""
    if len(route) < 2:
        return [list(p) for p in route]
    cum = [0.0]
    for a, b in itertools.pairwise(route):
        cum.append(cum[-1] + _m(a, b))
    total, out, j = cum[-1], [], 0
    if total == 0:
        return [list(route[0])] * n
    for i in range(n):
        d = total * i / (n - 1)
        while j < len(cum) - 2 and cum[j + 1] < d:
            j += 1
        seg = cum[j + 1] - cum[j]
        f = 0 if seg == 0 else (d - cum[j]) / seg
        a, b = route[j], route[j + 1]
        out.append([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f])
    return out


def _apart(a: list[list[float]], b: list[list[float]]) -> float:
    """The mean distance from each point of one to the nearest of the other, both ways (direction does not matter)."""
    one = sum(min(_m(p, q) for q in b) for p in a) / len(a)
    two = sum(min(_m(p, q) for q in a) for p in b) / len(b)
    return max(one, two)


def same(a: list[list[float]], b: list[list[float]]) -> bool:
    ends = (_m(a[0], b[0]) <= NEAR_ENDS_M and _m(a[-1], b[-1]) <= NEAR_ENDS_M) or (
        _m(a[0], b[-1]) <= NEAR_ENDS_M and _m(a[-1], b[0]) <= NEAR_ENDS_M)
    return ends and _apart(a, b) <= NEAR_LINE_M


def common(rides: list[dict[str, Any]], now: datetime, years: float = 5, more_than: int = 5) -> list[dict[str, Any]]:
    """Routes ridden more than `more_than` times in the last `years`, most ridden first.

    rides: {id, started, km, route: [[lat, lon], ...], indoor, title}."""
    since = now - timedelta(days=round(365.25 * years))
    groups: list[dict[str, Any]] = []
    for r in sorted(rides, key=lambda x: x["started"], reverse=True):  # the newest ride of each stands for it
        if r.get("indoor") or r["started"] < since or len(r.get("route") or []) < 2:
            continue
        pts = resample(r["route"])
        for g in groups:
            if abs(g["km"] - r["km"]) <= 0.25 * max(g["km"], r["km"], 1) and same(g["shape"], pts):
                g["rides"].append(r)
                break
        else:
            groups.append({"shape": pts, "km": r["km"], "rides": [r], "route": r["route"]})
    out = []
    for g in groups:
        if len(g["rides"]) <= more_than:
            continue
        rs = g["rides"]
        titles = [t for t in (x.get("title") for x in rs) if t]
        name = max(set(titles), key=titles.count) if titles else f"{round(g['km'])} km route"
        rid = hashlib.sha1(str(rs[0]["id"]).encode()).hexdigest()[:10]  # the newest ride's: steady until the next
        out.append({"id": rid, "name": name, "times": len(rs), "km": round(sum(x["km"] for x in rs) / len(rs), 1),
                    "first": min(x["started"] for x in rs), "last": max(x["started"] for x in rs),
                    "start": g["route"][0], "finish": g["route"][-1], "route": g["route"],
                    "loop": _m(g["route"][0], g["route"][-1]) <= NEAR_ENDS_M})  # fmt: skip
    return sorted(out, key=lambda x: -x["times"])


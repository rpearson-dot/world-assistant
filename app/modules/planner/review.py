"""The planner's month against what really happened: each month, what charging the car really cost and what it would
have cost had the planner been in charge, from its five-minute decisions and the unit rates in force. Sent to your phone
on the 1st and kept for the Planner tab, so whether to let it act is a figure in pounds, not a guess.

Pure (`judge`): from the month's decisions (each with the charger's state and the car's power at the time) and the
rates. The car's real energy is what it took while charging. The planner's version gives the same energy to the times
it said "charge", in order, at the charger's 4.6 kW, so it buys the same kWh at its own times. Energy its "charge" times
could not hold is priced at the month's dearest rate and said ("short"), so a planner that charges too little never
looks cheap. An estimate: the car's power is read every five minutes.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

STEP_H = 5 / 60  # a decision covers five minutes
CHARGER_KW = 4.6
KEY = "planner.review."


def rate_at(at: datetime, rates: list[tuple[datetime, datetime, float]]) -> float | None:
    for a, b, p in rates:
        if a <= at < b:
            return p
    return None


def judge(decisions: list[dict[str, Any]], rates: list[tuple[datetime, datetime, float]]) -> dict[str, Any]:
    """decisions: {at, plan, ha_plan, inputs: {status, car_w}} every five minutes, in time order."""
    dearest = max((p for _, _, p in rates), default=None)
    real_kwh = real_p = 0.0
    slots: list[float] = []  # the rate at each five minutes the planner said charge
    agree = judged = 0
    for d in decisions:
        i = d.get("inputs") or {}
        p = rate_at(d["at"], rates)
        if i.get("status") == "Charging":
            kwh = max(0.0, float(i.get("car_w") or CHARGER_KW * 1000)) / 1000 * STEP_H
            real_kwh += kwh
            real_p += kwh * (p if p is not None else dearest or 0.0)
        if d.get("plan") == "charge" and p is not None:
            slots.append(p)
        if d.get("ha_plan") not in (None, "unknown", "unavailable") and d.get("plan") != "off":
            judged += 1
            agree += d.get("plan") == d.get("ha_plan")
    left, plan_p = real_kwh, 0.0
    for p in slots:
        if left <= 0:
            break
        take = min(left, CHARGER_KW * STEP_H)
        plan_p += take * p
        left -= take
    short = max(0.0, left)
    plan_p += short * (dearest or 0.0)
    return {"checks": len(decisions), "car_kwh": round(real_kwh, 1), "real_pounds": round(real_p / 100, 2),
            "planner_pounds": round(plan_p / 100, 2), "saving_pounds": round((real_p - plan_p) / 100, 2),
            "real_average_p": round(real_p / real_kwh, 1) if real_kwh else None,
            "planner_average_p": round(plan_p / real_kwh, 1) if real_kwh else None,
            "short_kwh": round(short, 1), "agreed_pct": round(100 * agree / judged) if judged else None}  # fmt: skip


def last_month(today: date) -> tuple[date, date]:
    first = today.replace(day=1)
    start = (first - timedelta(days=1)).replace(day=1)
    return start, first


async def review(engine: Any, month_start: date, month_end: date, tz: Any) -> dict[str, Any]:
    from app.modules.octopus.usage import rates_between

    a = datetime(month_start.year, month_start.month, month_start.day, tzinfo=tz).astimezone(UTC)
    b = datetime(month_end.year, month_end.month, month_end.day, tzinfo=tz).astimezone(UTC)
    async with engine.connect() as conn:
        rows = [dict(r) for r in (await conn.execute(text(
            "SELECT at, plan, ha_plan, inputs FROM planner_decision WHERE at >= :a AND at < :b ORDER BY at"),
            {"a": a, "b": b})).mappings().all()]  # fmt: skip
        rates = await rates_between(conn, a, b)
        bill = (await conn.execute(text(
            "SELECT coalesce(sum(kwh), 0) FROM energy_usage WHERE direction = 'import' AND interval_start >= :a AND interval_start < :b"),
            {"a": a, "b": b})).scalar()  # fmt: skip
    out = {"month": month_start.strftime("%Y-%m"), **judge(rows, rates), "house_import_kwh": round(float(bill or 0), 1),
           "days_with_decisions": len({r["at"].astimezone(tz).date() for r in rows})}  # fmt: skip
    return out


def summary(r: dict[str, Any]) -> str:
    name = datetime.strptime(r["month"], "%Y-%m").strftime("%B")
    if not r["checks"]:
        return f"{name}: the planner made no decisions, so there is nothing to compare."
    if not r["car_kwh"]:
        return f"{name}: the car did not charge, so there is nothing to compare (the planner agreed with Home Assistant {r['agreed_pct']}% of the time)."
    verb = "saved" if r["saving_pounds"] > 0 else "cost"
    text = (f"{name}: charging the car ({r['car_kwh']} kWh) cost £{r['real_pounds']:.2f} at {r['real_average_p']}p/kWh. "
            f"With the planner in charge it would have been about £{r['planner_pounds']:.2f}, which {verb} "
            f"£{abs(r['saving_pounds']):.2f}.")  # fmt: skip
    if r["short_kwh"]:
        text += f" Its charging times held {r['car_kwh'] - r['short_kwh']:.1f} kWh of it; the rest is priced at the dearest rate."
    if r["agreed_pct"] is not None:
        text += f" It agreed with Home Assistant {r['agreed_pct']}% of the time."
    return text


async def planner_review(ctx: dict[str, Any]) -> Any:
    """On the 1st: last month reviewed, kept, and sent to your phone."""
    from app import notify
    from app.config import get_settings
    from app.jobs import tracked

    engine = ctx["engine"]

    async def work() -> dict[str, Any]:
        tz = ZoneInfo(get_settings().local_tz)
        start, end = last_month(datetime.now(tz).date())
        r = await review(engine, start, end, tz)
        r["summary"] = summary(r)
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                {"k": KEY + r["month"], "v": json.dumps(r, default=str)})  # fmt: skip
        if r["checks"]:
            await notify.send(engine, "home", f"planner:review:{r['month']}", "The planner's month", r["summary"], click="/planner")
        return {"month": r["month"], "saving_pounds": r.get("saving_pounds")}

    return await tracked(engine, "planner_review", work)


async def months(conn: Any) -> list[dict[str, Any]]:
    rows = (await conn.execute(text("SELECT value FROM app_setting WHERE key LIKE :k ORDER BY key DESC LIMIT 12"),
                               {"k": KEY + "%"})).scalars().all()  # fmt: skip
    return [json.loads(v) if isinstance(v, str) else v for v in rows]

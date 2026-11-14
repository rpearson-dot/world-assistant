"""Your electricity meter's half-hourly readings from Octopus (import, and export where there is an export meter),
kept so every plan can be checked against what was really bought and sold. Octopus publishes a day's readings the
next day; the worker fetches what is new each morning, back a year the first time (and back-fills to a year where
an earlier version fetched only 90 days).

REST: /v1/electricity-meter-points/{mpan}/meters/{serial}/consumption/ (HTTP Basic, the API key as the user name).
The meter points and serials come from the account (/v1/accounts/{account}/). The reading and the sums are pure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.modules.octopus.octopus import BASE, OctopusError, _parse_time

FIRST_DAYS = 366  # a year: what the tariff comparison counts at most


def meters(account: dict[str, Any]) -> list[dict[str, Any]]:
    """Every electricity meter on the account: its point (MPAN), serial and whether it is the export meter. A point
    with several meters (a meter exchange) keeps each; readings are asked of each and the empty ones yield nothing."""
    out = []
    for prop in account.get("properties") or []:
        for point in prop.get("electricity_meter_points") or []:
            for m in point.get("meters") or []:
                if point.get("mpan") and m.get("serial_number"):
                    out.append({"mpan": str(point["mpan"]), "serial": str(m["serial_number"]),
                                "direction": "export" if point.get("is_export") else "import"})  # fmt: skip
    return out


def readings(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Half-hours from a consumption answer (kWh for an SMETS2 electricity meter)."""
    out = []
    for r in body.get("results") or []:
        start = _parse_time(r.get("interval_start"))
        try:
            kwh = float(r.get("consumption"))
        except (TypeError, ValueError):
            continue
        if start is not None:
            out.append({"start": start.astimezone(UTC), "kwh": kwh})
    return out


async def account(client: httpx.AsyncClient, number: str, key: str) -> dict[str, Any]:
    r = await client.get(f"{BASE}/v1/accounts/{number}/", auth=(key, ""), timeout=30)
    if r.status_code in (401, 403):
        raise OctopusError("Octopus refused the API key (check octopus_api_key and the account number)")
    r.raise_for_status()
    return r.json()


async def fetch(client: httpx.AsyncClient, key: str, mpan: str, serial: str, since: datetime, until: datetime) -> list[dict[str, Any]]:
    url = f"{BASE}/v1/electricity-meter-points/{mpan}/meters/{serial}/consumption/"
    params: dict[str, Any] | None = {"period_from": since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                     "period_to": until.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                     "page_size": 25000, "order_by": "period"}  # fmt: skip
    out: list[dict[str, Any]] = []
    for _ in range(10):  # 25,000 half-hours a page: about 17 months
        r = await client.get(url, params=params, auth=(key, ""), timeout=60)
        if r.status_code == 404:
            return out  # an old meter on the point: no readings
        r.raise_for_status()
        body = r.json()
        out += readings(body)
        if not body.get("next"):
            break
        url, params = body["next"], None
    return out


def band(at: datetime, rates: list[tuple[datetime, datetime, float]]) -> float | None:
    """The unit rate (p/kWh) in force at a half-hour's start, from (valid_from, valid_to, p/kWh) rows."""
    for a, b, p in rates:
        if a <= at < b:
            return p
    return None


def days(rows: list[dict[str, Any]], rates: list[tuple[datetime, datetime, float]], tz: Any,
         dear_from: int = 16, dear_to: int = 19) -> list[dict[str, Any]]:
    """Each day: kWh bought and sold, what was bought cost (where the rate is known), and the kWh bought in the dear
    hours (16:00 to 19:00 by default) and at each rate."""
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        local = r["start"].astimezone(tz)
        d = by.setdefault(local.date().isoformat(), {"day": local.date().isoformat(), "import_kwh": 0.0, "export_kwh": 0.0,
                                                      "cost_p": 0.0, "priced_kwh": 0.0, "dear_kwh": 0.0, "by_rate": {}})  # fmt: skip
        if r["direction"] == "export":
            d["export_kwh"] += r["kwh"]
            continue
        d["import_kwh"] += r["kwh"]
        if dear_from <= local.hour < dear_to:
            d["dear_kwh"] += r["kwh"]
        p = band(r["start"], rates)
        if p is not None:
            d["cost_p"] += r["kwh"] * p
            d["priced_kwh"] += r["kwh"]
            key = f"{p:g}"
            d["by_rate"][key] = d["by_rate"].get(key, 0.0) + r["kwh"]
    out = []
    for d in sorted(by.values(), key=lambda x: x["day"]):
        out.append({**d, **{k: round(d[k], 2) for k in ("import_kwh", "export_kwh", "cost_p", "priced_kwh", "dear_kwh")},
                    "by_rate": {k: round(v, 2) for k, v in sorted(d["by_rate"].items(), key=lambda kv: float(kv[0]))}})  # fmt: skip
    return out


def since_for(last: datetime | None, now: datetime) -> datetime:
    return (last + timedelta(minutes=30)) if last else now - timedelta(days=FIRST_DAYS)


# --- keeping them, and the job ------------------------------------------------------------------------------------


async def rates_between(conn: Any, start: datetime, end: datetime) -> list[tuple[datetime, datetime, float]]:
    from sqlalchemy import text

    rows = (await conn.execute(text(
        "SELECT valid_from, coalesce(valid_to, 'infinity'::timestamptz), pence_inc_vat FROM energy_rate "
        "WHERE valid_from < :b AND coalesce(valid_to, 'infinity'::timestamptz) > :a ORDER BY valid_from, fetched_at DESC"),
        {"a": start, "b": end})).all()  # fmt: skip
    return [(r[0], r[1], float(r[2])) for r in rows]


async def sync_usage(engine: Any, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    from sqlalchemy import text

    from app.config import get_settings

    s = get_settings()
    if not (s.octopus_account.strip() and s.octopus_api_key):
        return {"skipped": "needs the account number and the API key"}
    own = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": "WorldAssistant"})
    out: dict[str, Any] = {}
    try:
        acct = await account(client, s.octopus_account.strip(), s.octopus_api_key)
        now = datetime.now(UTC)
        for m in meters(acct):
            async with engine.connect() as conn:
                last = (await conn.execute(text("SELECT max(interval_start) FROM energy_usage WHERE direction = :d"),
                                           {"d": m["direction"]})).scalar()  # fmt: skip
                first = (await conn.execute(text("SELECT min(interval_start) FROM energy_usage WHERE direction = :d"),
                                            {"d": m["direction"]})).scalar()  # fmt: skip
            rows = await fetch(client, s.octopus_api_key, m["mpan"], m["serial"], since_for(last, now), now)
            year = now - timedelta(days=FIRST_DAYS)
            if first is not None and first > year + timedelta(days=2):  # the year before what is kept
                rows += await fetch(client, s.octopus_api_key, m["mpan"], m["serial"], year, first)
            if rows:
                async with engine.begin() as conn:
                    await conn.execute(text(
                        "INSERT INTO energy_usage (direction, interval_start, kwh, mpan) VALUES (:d, :s, :k, :m) "
                        "ON CONFLICT (direction, interval_start) DO UPDATE SET kwh = EXCLUDED.kwh, fetched_at = now()"),
                        [{"d": m["direction"], "s": r["start"], "k": r["kwh"], "m": m["mpan"]} for r in rows])  # fmt: skip
            out[m["direction"]] = out.get(m["direction"], 0) + len(rows)
    finally:
        if own:
            await client.aclose()
    return out


async def energy_usage(ctx: dict[str, Any]) -> Any:
    from app.jobs import tracked

    async def work() -> dict[str, Any]:
        return await sync_usage(ctx["engine"])

    return await tracked(ctx["engine"], "energy_usage", work)

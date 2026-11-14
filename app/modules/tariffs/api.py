"""Tariff comparison (/v1/tariffs, the Energy → Tariffs tab): your real half-hourly consumption, from the Octopus
module's meter readings, priced at each tariff's rates **today**, as used and with your house battery moving use into
each tariff's cheap windows; ranked, with the best import and export to move to.

Octopus's tariffs come from its public API; any other supplier's you add by hand from a quote (bands, standing
charge, export rate). Readings count from a date you set (when the house last changed: solar fitted, gas removed) and
never more than a year back.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.jobs import tracked
from app.modules.tariffs import catalogue, price

router = APIRouter(prefix="/v1/tariffs", tags=["tariffs"], dependencies=[Depends(require_token)])
SETTINGS, RESULT = "tariffs.settings", "tariffs.result"
DEFAULTS: dict[str, Any] = {"count_from": None, "capacity_kwh": 32.0, "charge_kw": 5.0, "region": "", "manual": []}
MAX_DAYS = 366


async def _get(conn: Any, key: str) -> Any:
    v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": key})).scalar()
    return json.loads(v) if isinstance(v, str) else v


async def _put(conn: Any, key: str, value: Any) -> None:
    await conn.execute(text(
        "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": key, "v": json.dumps(value, default=str)})  # fmt: skip


async def settings(conn: Any) -> dict[str, Any]:
    return {**DEFAULTS, **(await _get(conn, SETTINGS) or {})}


def since(st: dict[str, Any], now: datetime, tz: Any) -> datetime:
    """Where the readings start: the date set, but never more than a year back."""
    floor = now - timedelta(days=MAX_DAYS)
    if st.get("count_from"):
        d = date.fromisoformat(str(st["count_from"]))
        return max(floor, datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(UTC))
    return floor


def manual_tariffs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tariffs typed from a quote, as the comparison takes them: the import, and its export when one is given (then
    the two go together)."""
    out = []
    for m in rows:
        fam = f"manual:{m['id']}" if m.get("export_p") is not None else None
        base = {"supplier": m.get("supplier") or "", "source": "manual", "averaged": False, "pairs_with": fam,
                "exit_fee_p": round(float(m.get("exit_fee_pounds") or 0) * 100), "conditions": [m["notes"]] if m.get("notes") else []}  # fmt: skip
        out.append({**base, "code": f"manual:{m['id']}", "name": m["name"], "direction": "import",
                    "standing_p": float(m.get("standing_p") or 0), "pattern": price.bands(m["bands"])})  # fmt: skip
        if fam:
            out.append({**base, "code": f"manual:{m['id']}:export", "name": f"{m['name']} export", "direction": "export",
                        "pattern": [float(m["export_p"])] * price.SLOTS})  # fmt: skip
    return out


async def work_out(engine: Any, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch Octopus's tariffs, price the readings at each, keep the answer."""
    from app.modules.octopus import octopus as O
    from app.modules.octopus import store as estore

    tz, now = ZoneInfo(get_settings().local_tz), datetime.now(UTC)
    async with engine.connect() as conn:
        st = await settings(conn)
        start = since(st, now, tz)
        rows = [dict(r) for r in (await conn.execute(text(
            "SELECT direction, interval_start AS start, kwh FROM energy_usage WHERE interval_start >= :a ORDER BY interval_start"),
            {"a": start})).mappings().all()]  # fmt: skip
    imports = [r for r in rows if r["direction"] == "import"]
    exports = [r for r in rows if r["direction"] == "export"]
    if not imports:
        why = ("No meter readings yet: the Octopus module fetches them each morning (it needs the account number and API "
               "key), then press Work it out again on Energy → Tariffs")
        out = {"error": why, "not_set_up": why, "computed_at": now.isoformat()}
        async with engine.begin() as conn:
            await _put(conn, RESULT, out)
        return out
    current, _ = await estore.load(engine)
    region = (st.get("region") or catalogue.region_of(current) or "").upper()
    if not region:
        raise HTTPException(422, "your region is not known: set it here (the letter at the end of your tariff code)")
    own = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": "world-assistant (self-hosted)"})
    try:
        found = await catalogue.octopus(client, region, now, also=O.product_of(current) if current else None)
    finally:
        if own:
            await client.aclose()
    tariffs, skipped = [], []
    for t in found:
        if t.get("error"):
            skipped.append({"name": t["name"], "why": t["error"]})
            continue
        pat, averaged = price.pattern(t["rates"], tz, now)
        if pat is None:
            skipped.append({"name": t["name"], "why": "no rates for today"})
            continue
        t = {k: v for k, v in t.items() if k != "rates"}
        tariffs.append({**t, "code": t["tariff"], "pattern": pat, "averaged": averaged})  # yours matches by its code
    tariffs += manual_tariffs(st.get("manual") or [])
    battery = {"capacity_kwh": st["capacity_kwh"], "charge_kw": st["charge_kw"]} if st.get("capacity_kwh") and st.get("charge_kw") else None
    res = price.compare(imports, exports, tariffs, tz, current=current, battery=battery)
    for side in ("import", "export"):
        for r in res[side]:
            r["pattern"] = next((t["pattern"] for t in tariffs if t["code"] == r["code"]), None)
    out = {**res, "from": start.astimezone(tz).date().isoformat(), "to": imports[-1]["start"].astimezone(tz).date().isoformat(),
           "region": region, "current": current, "skipped": skipped, "computed_at": now.isoformat()}  # fmt: skip
    async with engine.begin() as conn:
        await _put(conn, RESULT, out)
    return out


@router.get("")
async def get_result(request: Request) -> dict[str, Any]:
    """The last comparison (worked out only when asked: POST /v1/tariffs/compare) and the settings."""
    async with request.app.state.engine.connect() as conn:
        return {"result": await _get(conn, RESULT), "settings": await settings(conn)}


@router.post("/compare")
async def compare_now(request: Request) -> dict[str, Any]:
    """Work the comparison out again now (it fetches every Octopus tariff: about half a minute)."""
    try:
        return await tracked(request.app.state.engine, "tariffs_compare", lambda: work_out(request.app.state.engine))
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Octopus: {exc}") from exc


class Band(BaseModel):
    from_: str = Field(alias="from", pattern=r"^([01]\d|2[0-4]):(00|30)$")
    to: str = Field(pattern=r"^([01]\d|2[0-4]):(00|30)$")
    pence: float = Field(ge=0, le=200)

    model_config = {"populate_by_name": True}


class Manual(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    supplier: str = Field("", max_length=80)
    standing_p: float = Field(0, ge=0, le=300)
    bands: list[Band] = Field(min_length=1, max_length=12)
    export_p: float | None = Field(None, ge=0, le=100)
    exit_fee_pounds: float = Field(0, ge=0, le=1000)
    notes: str = Field("", max_length=300)


class SettingsIn(BaseModel):
    count_from: date | None = None
    capacity_kwh: float | None = Field(32.0, gt=0, le=100)
    charge_kw: float | None = Field(5.0, gt=0, le=30)
    region: str = Field("", pattern=r"^[A-Pa-p]?$")
    manual: list[Manual] = Field(default_factory=list, max_length=20)

    @field_validator("count_from")
    @classmethod
    def _not_future(cls, v: date | None) -> date | None:
        if v and v > date.today():
            raise ValueError("the date is in the future")
        return v


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """Count readings from (when the house last changed); your house battery's usable kWh and charge rate (for the
    shifted costs); your region if your tariff code does not say; and tariffs from other suppliers' quotes."""
    ids = [m.id for m in body.manual]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "two tariffs have the same id")
    st = body.model_dump(mode="json", by_alias=True)
    st["region"] = st["region"].upper()
    async with request.app.state.engine.begin() as conn:
        await _put(conn, SETTINGS, st)
    return st

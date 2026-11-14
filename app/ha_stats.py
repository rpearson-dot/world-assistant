"""Reading Home Assistant (for the planner, the solar forecast and any module): entity states (REST /api/states), and the long-term statistics it keeps
forever (hourly energy, through its websocket API: recorder/list_statistic_ids, recorder/statistics_during_period).
Uses the address and token set in Settings → Home Assistant. Reading only."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import websockets

from app.ha_discover import ha_api, live_states


class HassError(Exception):
    pass


async def access(engine: Any) -> tuple[str, str]:
    a = await ha_api(engine)
    if a is None:
        raise HassError("Home Assistant's address and token are not set (Settings → Home Assistant)")
    return a


async def states(engine: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    url, token = await access(engine)
    got = await live_states(url, token)
    if "error" in got:
        raise HassError(got["error"])
    want = set(ids)
    return {s["entity_id"]: s for s in got["states"] if s.get("entity_id") in want}


def _ws_url(url: str) -> str:
    u = url.rstrip("/")
    return ("wss://" + u[8:] if u.startswith("https://") else "ws://" + u.removeprefix("http://")) + "/api/websocket"


async def _ask(url: str, token: str, messages: list[dict[str, Any]]) -> list[Any]:
    """One websocket session: authenticate, send each message, collect each result in order."""
    out = []
    async with websockets.connect(_ws_url(url), max_size=64 * 1024 * 1024, open_timeout=15) as ws:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            raise HassError("not Home Assistant's websocket")
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(await ws.recv()).get("type") != "auth_ok":
            raise HassError("Home Assistant refused the token")
        for i, m in enumerate(messages, start=1):
            await ws.send(json.dumps({"id": i, **m}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == i and r.get("type") == "result":
                    if not r.get("success"):
                        raise HassError(f"{m['type']}: {(r.get('error') or {}).get('message')}")
                    out.append(r.get("result"))
                    break
    return out


async def energy_statistics(engine: Any) -> list[dict[str, Any]]:
    """Every statistic Home Assistant keeps a running total of in kWh (the candidates for the house's demand)."""
    url, token = await access(engine)
    (ids,) = await _ask(url, token, [{"type": "recorder/list_statistic_ids", "statistic_type": "sum"}])
    return [{"id": s["statistic_id"], "name": s.get("name") or s["statistic_id"]} for s in ids or []
            if (s.get("statistics_unit_of_measurement") or s.get("unit_of_measurement")) == "kWh"]  # fmt: skip


async def hourly_kwh(engine: Any, statistic_id: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """kWh in each hour from a running-total statistic (its hourly change)."""
    url, token = await access(engine)
    (res,) = await _ask(url, token, [{"type": "recorder/statistics_during_period", "start_time": start.isoformat(),
                                      "end_time": end.isoformat(), "statistic_ids": [statistic_id], "period": "hour",
                                      "types": ["change"], "units": {"energy": "kWh"}}])  # fmt: skip
    out = []
    for r in (res or {}).get(statistic_id) or []:
        at = r.get("start")
        when = datetime.fromtimestamp(at / 1000, tz=start.tzinfo) if isinstance(at, (int, float)) else datetime.fromisoformat(at)
        if r.get("change") is not None and r["change"] >= 0:
            out.append((when, float(r["change"])))
    return out

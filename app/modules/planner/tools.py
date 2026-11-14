"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props


async def charging_plan(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.planner.api import charging

    c = await charging(request, days=7)
    last = c["latest"]
    if not last:
        return {"text": "The planner has not made a decision yet.", "data": c}
    text = f"World Assistant would {last['plan']}: {last['why']}. Home Assistant's plan is {last['ha_plan'] or 'not known'}."
    if c["agreed_pct"] is not None:
        text += f" They agreed {c['agreed_pct']}% of the time this week."
    return {"text": text, "data": c}


async def evening_demand(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.planner.api import demand_report

    d = await demand_report(request.app.state.engine, request.app.state.redis)
    if d.get("error"):
        return {"text": d["error"], "data": d}
    f, t = d["fit"], d.get("tomorrow")
    if not f.get("enough"):
        return {"text": f["why"], "data": d}
    text = f"Evening use is {f['verdict']} from the cold (R² {f['r2']} over {f['days']} evenings)."
    if t:
        text += f" Tomorrow evening likely {t['likely_kwh']} kWh, 9 in 10 under {t['nine_in_ten_kwh']} kWh."
    return {"text": text, "data": d}


TOOLS: list[Tool] = [
    Tool("charging_plan", "What World Assistant would do with the car's charging now and why, beside Home Assistant's plan.",
         {"type": "object", "properties": _props()}, charging_plan),
    Tool("evening_demand", "How predictable the house's evening electricity use is from the weather, and tomorrow evening's likely use.",
         {"type": "object", "properties": _props()}, evening_demand),
]

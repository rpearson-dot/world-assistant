"""The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props


async def solar_forecast(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.solar.api import summary

    d = await summary(request.app.state.engine)
    if not d["fetched_at"]:
        return {"text": "There's no solar forecast yet: the arrays need setting up on Energy → Solar.", "data": d}
    text = f"The panels should make about {d['today_kwh']:.1f} kWh today, {d['remaining_today_kwh']:.1f} kWh of it still to come"
    if d["tomorrow_kwh"] is not None:
        text += f", and about {d['tomorrow_kwh']:.1f} kWh tomorrow"
    return {"text": text + ".", "data": {k: v for k, v in d.items() if not k.endswith("hours")}}


TOOLS: list[Tool] = [
    Tool("solar_forecast", "How much the solar panels should make today (and what is still to come) and tomorrow.",
         {"type": "object", "properties": _props()}, solar_forecast),
]

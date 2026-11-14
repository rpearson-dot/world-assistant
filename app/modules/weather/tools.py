"""The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props, _wrap


async def weather(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.weather.api import weather as get

    w = await _wrap(get(request))
    d0, d1 = w["days"][0], w["days"][1]
    text = (f"Now {round(w['now']['temp_c'])}° and {w['now']['words']}. Today {round(d0['low_c'])}° to {round(d0['high_c'])}°"
            f"{', ' + str(d0['rain_mm']) + ' mm rain' if d0['rain_mm'] else ''}. Tomorrow {round(d1['low_c'])}° to {round(d1['high_c'])}°, "
            f"{d1['mostly']}; the evening about {d1['evening_c']}°.")  # fmt: skip
    return {"text": text, "data": w}


TOOLS: list[Tool] = [
    Tool("weather", "The weather at home now, today and the next days (temperature, rain, sun, how cold the evenings will be).",
         {"type": "object", "properties": _props()}, weather),
]

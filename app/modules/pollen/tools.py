"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import (
    Tool,
    _props,
    _wrap,
)
from app.modules.pollen.pollen import near as pollen_near


async def pollen_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    place = str(args.get("place") or "") or None
    res = await _wrap(pollen_near(request, place=place, hourly=False))
    where = res.get("place") or place
    risk = (res.get("allergy_risk_label") or [None])[0]
    high = [
        f"{a['name']} {a['labels'][0]}"
        for a in res["allergens"]
        if (a["levels"][0] or 0) >= 1 and a.get("name")
    ][:5]
    parts = []
    if risk:
        parts.append(f"allergy risk today is {risk}")
    parts.append("pollen today: " + ", ".join(high) if high else "no pollen is reported today")
    return {"text": f"Near {where}, " + "; ".join(parts) + ".", "data": res}


TOOLS: list[Tool] = [
    Tool("pollen", "Pollen levels and allergy risk near a saved place, today and the next three days.",
         {"type": "object", "properties": _props(place=("string", "id of a saved place; default: home"))}, pollen_tool),
]

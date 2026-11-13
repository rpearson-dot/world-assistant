"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request

from app.assistant_base import (
    Tool,
    ToolError,
    _props,
)
from app.config import get_settings
from app.modules.octopus import sessions as energy_sessions
from app.modules.octopus import speech as energy_speech
from app.modules.octopus import store as energy_store
from app.modules.octopus import summary as energy_summary


async def energy_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    hours = float(args.get("hours") or 3)
    tariff, periods = await energy_store.load(request.app.state.engine)
    now = datetime.now(UTC)
    res = energy_summary.summarise(tariff, periods, now, min(max(hours, 0.5), 12))
    if not res["available"]:
        raise ToolError("There are no electricity rates yet. Set up the Octopus account or tariff (docs/energy.md).")
    res["sessions"] = energy_sessions.summarise(await energy_store.load_sessions(request.app.state.engine), now)
    return {"text": energy_speech.sentence(res, ZoneInfo(get_settings().local_tz), now), "data": res}


TOOLS: list[Tool] = [
    Tool("electricity", "Electricity price now and next, the next cheap and peak times, the cheapest block of hours, and Saving Sessions.",
         {"type": "object", "properties": _props(hours=("number", "length of the cheapest block, default 3"))}, energy_tool),
]

"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props, _wrap


async def bus_log(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.buslog.api import status

    s = await _wrap(status(request))
    line = s["settings"]["line"]
    head = (f"The {line} recorder is recording ({s['why']})." if s["recording"]
            else f"The {line} recorder is not recording now ({s['why']}).")  # fmt: skip
    parts = [head]
    for src in s["today"]["sources"]:
        parts.append(f"{src['source']}: {src['ok']} with data, {src['empty']} empty, {src['failed']} failed today.")
    if s["next_days"]:
        parts.append("Next recording day: " + s["next_days"][0]["day"] + ".")
    return {"text": " ".join(parts), "data": s}


TOOLS: list[Tool] = [
    Tool("bus_log", "Whether the bus route recorder (the 213 in the morning school window) is recording, and how "
         "today's requests to TfL and BODS went (with data, empty or failed).",
         {"type": "object", "properties": _props()}, bus_log),
]

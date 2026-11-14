"""The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props


async def what_is_on_the_network(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.discovery.api import discovery

    d = await discovery(request)
    named = [f"{x['label']} ({x['what']})" for x in d["devices"] if x["kind"]][:12]
    text = f"{d['identified']} of {len(d['devices'])} devices recognised" + (": " + "; ".join(named) if named else "") + "."
    if d["suggestions"]:
        text += f" {d['suggestions']} could do more with a module: see In the home → Discovery."
    return {"text": text, "data": d}


TOOLS: list[Tool] = [
    Tool("what_is_on_the_network", "What the devices on the home network probably are, and modules that would suit them.",
         {"type": "object", "properties": _props()}, what_is_on_the_network),
]

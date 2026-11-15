"""The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props


async def pihole_groups(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.pihole.api import groups

    d = await groups(request)
    parts = [f"{g['name']}: {len(g['devices'])} devices, {g['lookups_week']} lookups past your lists this week" for g in d["groups"]]
    return {"text": "; ".join(parts) + "." if parts else "No Pi-hole groups of your own.", "data": d}


TOOLS: list[Tool] = [
    Tool("pihole_groups", "Your Pi-hole groups (Kids, Adults, IoT...), their devices, and how much got past your lists this week.",
         {"type": "object", "properties": _props()}, pihole_groups),
]

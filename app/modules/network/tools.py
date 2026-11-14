"""The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy import text

from app.assistant_base import Tool, _props


async def network_devices(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT coalesce(alias, name, ip, mac) AS label, last_seen, first_seen FROM net_device "
            "WHERE last_seen > now() - interval '1 hour' ORDER BY last_seen DESC"))).mappings().all()  # fmt: skip
        new = (await conn.execute(text("SELECT coalesce(alias, name, ip, mac) FROM net_device WHERE first_seen > now() - interval '1 day'"))).scalars().all()
    text_ = f"{len(rows)} devices seen on the network in the last hour."
    if new:
        text_ += f" New today: {', '.join(new)}."
    return {"text": text_, "data": {"recent": [dict(r) for r in rows], "new_today": list(new)}}


TOOLS: list[Tool] = [
    Tool("network_devices", "Which devices are on the home network now, and any that joined for the first time today.",
         {"type": "object", "properties": _props()}, network_devices),
]

"""The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props


async def best_tariff(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.tariffs.api import RESULT, _get

    async with request.app.state.engine.connect() as conn:
        r = await _get(conn, RESULT)
    if not r:
        return {"text": "The tariff comparison has not been worked out yet (Energy → Tariffs).", "data": None}
    if r.get("error"):
        return {"text": r["error"], "data": r}
    s = r.get("suggestion")
    if not s:
        return {"text": "No tariff could be priced.", "data": r}
    pair = s["import_name"] + (f" with {s['export_name']}" if s.get("export_name") else "")
    if s.get("already_best"):
        text = f"You're already on the cheapest for your use: {pair}, about £{s['year_pounds']:,} a year."
    else:
        text = f"The cheapest for your use is {pair}, about £{s['year_pounds']:,} a year"
        text += f", £{s['saving_pounds']:,} less than now." if s.get("saving_pounds") is not None else "."
    return {"text": text + f" From your readings {r['from']} to {r['to']}, at today's rates.", "data": s}


TOOLS: list[Tool] = [
    Tool("best_tariff", "Which electricity tariff (import and export) would cost least for this house's real use, at today's "
         "rates, and the saving against the current one.", {"type": "object", "properties": _props()}, best_tariff),
]

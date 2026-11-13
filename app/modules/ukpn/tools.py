"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request

from app.assistant_base import (
    Tool,
    _props,
    _wrap,
)
from app.config import get_settings


async def power_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.ukpn.power import cuts, history, supply, works

    place = args.get("place")
    what = str(args.get("about") or "cuts")
    if what == "supply":
        s = await _wrap(supply(request, place))
        if not s["checked"]:
            return {"text": "The network around this place has not been looked up yet (it is, each night).", "data": s}
        if not s["on_network"]:
            other = f" It is supplied by {s['independent_operator']}." if s["independent_operator"] else ""
            return {"text": "This place is not on UK Power Networks' network." + other, "data": s}
        chain = [f"{label} {s[k]['name']}" for k, label in (("grid_supply_point", "grid supply point"),
                 ("primary", "primary substation"), ("secondary", "local substation")) if s.get(k)]  # fmt: skip
        rota = f" Rota block {s['rota_block']['code']}." if s.get("rota_block") else ""
        cut = " There is a power cut here now." if s["power_cut_now"] else " No power cut here now."
        return {"text": "Fed from " + ", ".join(chain) + "." + rota + cut, "data": s}
    if what == "history":
        h = await _wrap(history(request, place, int(args.get("days") or 365), 200))
        if not h["count"]:
            return {"text": "No power cuts recorded there" + (f" since {h['recording_since'][:10]}." if h["recording_since"] else "."), "data": h}
        longest = f" The longest lasted {h['longest_hours']:g} hours." if h["longest_hours"] else ""
        late = f" Power came back later than first promised {h['late']} time(s)." if h["late"] else ""
        return {"text": f"{h['unplanned']} unplanned and {h['planned']} planned power cut(s) recorded there in the last "
                        f"{h['days']} days.{longest}{late}", "data": h}  # fmt: skip
    if what == "works":
        w = await _wrap(works(request, place, float(args.get("km") or 1), None))
        if not w["count"]:
            return {"text": "No UK Power Networks street works near there.", "data": w}
        bits = [f"{x['street'] or 'a street'}" + (f" ({x['traffic']})" if x.get("traffic") else "") for x in w["works"][:4]]
        return {"text": f"{w['open']} under way and {w['proposed']} planned nearby: " + "; ".join(bits) + ".", "data": w}
    r = await _wrap(cuts(request, place, float(args.get("km") or 5)))
    if not r["count"]:
        return {"text": "No power cuts reported near there.", "data": r}
    head = "There is a power cut at this place. " if r["at_place"] else ""
    bits = []
    for x in r["cuts"][:3]:
        kind = "planned" if x["planned"] else "unplanned" if x["planned"] is False else ""
        eta = f", power back by {datetime.fromisoformat(x['restore_by']).astimezone(ZoneInfo(get_settings().local_tz)):%H:%M}" if x["restore_by"] else ""
        bits.append(f"{kind} cut".strip() + (f" {x['km']} km away" if x["km"] is not None else "")
                    + (f" affecting {x['customers']} homes" if x["customers"] else "") + eta)  # fmt: skip
    return {"text": f"{head}{r['count']} power cut(s) nearby: " + "; ".join(bits) + ".", "data": r}


TOOLS: list[Tool] = [
    Tool("power", "Electricity from UK Power Networks: power cuts near a saved place (default), their street works, what "
         "supplies the place (substations), or past cuts there.",
         {"type": "object", "properties": _props(
             about=("string", "cuts (default), history, works or supply"), days=("integer", "history: how far back, default 365"),
             place=("string", "id of a saved place; default: home"), km=("number", "how far to look"))}, power_tool),
]

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


def _closure_text(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No lifts, escalators or entrances reported closed."
    return "Closed now: " + " ".join(
        f"{i['description'].rstrip('.')}." + (f" (until {i['until'][:10]})" if i.get("until") else "") for i in items[:4]
    ) + (f" And {len(items) - 4} more." if len(items) > 4 else "")


async def station_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.tflstations.stations import alerts, near, one, search

    name = str(args.get("name") or "").strip()
    if name:
        found = await _wrap(search(request, name, 3))
        if not found["stations"]:
            if not found["loaded"]:
                return {"text": "TfL's station data has not been loaded yet.", "data": found}
            return {"text": f"No TfL station matches {name!r}. TfL covers Underground, Overground, DLR, "
                            "Elizabeth line and tram stations.", "data": found}  # fmt: skip
        st = await _wrap(one(found["stations"][0]["id"], request))
        fac = st["facilities"].get("Facility", {})
        has = ", ".join(f"{k.lower()} {v}" if v not in ("yes", "Yes") else k.lower()
                        for k, v in fac.items() if k in ("Lifts", "Toilets", "Escalators", "Car park") and v)  # fmt: skip
        head = (f"{st['name']}" + (f", zone {st['zone']}" if st.get("zone") else "")
                + (f": {', '.join(st['lines'][:6])}." if st["lines"] else ".")
                + (f" {st['entrances']} entrances, {st['platforms']} platforms." if st["entrances"] else "")
                + (f" Has {has}." if has else "") + (f" Step-free: {st['step_free']}." if st.get("step_free") else ""))  # fmt: skip
        items = st["closures"] if not args.get("lifts_only") else [c for c in st["closures"] if c["kind"] in ("lift", "step_free")]
        return {"text": f"{head} {_closure_text(items)}", "data": st}
    if args.get("place") or not args.get("everywhere"):
        r = await _wrap(near(request, args.get("place"), float(args.get("km") or 2), 8))
        if not r["stations"]:
            return {"text": "No TfL stations within that distance of the place.", "data": r}
        lines = []
        for st in r["stations"]:
            items = [c for c in st["closures"] if not args.get("lifts_only") or c["kind"] in ("lift", "step_free")]
            lines.append(f"{st['name']} ({st['km']} km): " + (_closure_text(items) if items else "nothing closed"))
        return {"text": "Near " + r["place"] + ": " + "; ".join(lines) + ".", "data": r}
    r = await _wrap(alerts(request, None, bool(args.get("lifts_only"))))
    return {"text": f"{r['count']} closures across TfL stations now.", "data": r}


TOOLS: list[Tool] = [
    Tool("station", "A TfL station's lines, zone, entrances, platforms, facilities (lifts, toilets, step-free) and what is closed there now, "
         "especially lifts out of service; or the stations near a saved place and their closures.",
         {"type": "object", "properties": _props(
             name=("string", "station name, e.g. Waterloo; omit to list stations near a place"),
             place=("string", "id of a saved place; default: home"), km=("number", "how far from the place, default 2"),
             lifts_only=("boolean", "only lift and step-free closures"))}, station_tool),
]

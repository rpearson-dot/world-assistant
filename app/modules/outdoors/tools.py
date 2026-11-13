"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _need, _props, _wrap


# --- Outdoors ----------------------------------------------------------------------------------------------------
def _outdoor_text(it: dict[str, Any]) -> str:
    ps = it.get("products") or []
    if not ps:
        return f"{it['name']}: not priced yet."
    shop = {"gooutdoors": "GO Outdoors", "decathlon": "Decathlon", "bikesy": "Bikesy", "tradeinn": "Tradeinn",
            "sigma": "Sigma Sports"}
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}

    def one(p: dict[str, Any]) -> str:
        s = sym.get(p.get("currency") or "GBP", (p.get("currency") or "") + " ")
        price = f"{s}{p['price']:.2f}" if p.get("price") is not None else "no price"
        extra = []
        if p.get("dropped") and p.get("previous_price") is not None:
            extra.append(f"down from £{p['previous_price']:.2f}")
        if p.get("was_price"):
            extra.append(f"was £{p['was_price']:.2f}")
        if p.get("lowest_price") is not None and p.get("price") is not None and p["lowest_price"] < p["price"]:
            extra.append(f"lowest seen £{p['lowest_price']:.2f}")
        if p.get("available") is False:
            extra.append("out of stock")
        if p.get("error"):
            extra.append(f"could not check it lately: {p['error']}")
        at = p.get("shop") and f"{p['shop']} (via {shop.get(p['retailer'], p['retailer'])})" or shop.get(p["retailer"], p["retailer"])
        return f"{p['name']} at {at}, {price}" + (f" ({', '.join(extra)})" if extra else "")

    shown = ps[:3]
    fav = next((p for p in it.get("preferred") or [] if p not in shown), None)  # the preferred shop, even if dearer
    return f"{it['name']}: " + "; ".join(one(p) for p in shown + ([fav] if fav else [])) + "."


async def outdoors_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.outdoors.outdoors import overview

    data = await _wrap(overview(request))
    if not data["items"]:
        return {"text": f"The {data['list']['name']} list in KitchenOwl is empty.", "data": data}
    want = str(args.get("name") or "").strip().lower()
    items = [i for i in data["items"] if not want or want in i["name"].lower()]
    if not items:
        return {"text": f"Nothing called {args.get('name')!r} on the Outdoors list.", "data": data}
    drops = sum(len(i["dropped"]) for i in items)
    head = f"{drops} price drop{'s' if drops != 1 else ''} this week. " if drops else ""
    return {"text": head + " ".join(_outdoor_text(i) for i in items), "data": {**data, "items": items}}


async def outdoors_add_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.outdoors.outdoors import ItemIn, add_outdoors

    bits = [str(args["link"]).strip()] if args.get("link") else []
    if args.get("max_price"):
        bits.append(f"under £{float(args['max_price']):g}")
    r = await _wrap(add_outdoors(request, ItemIn(name=_need(args, "name"), description=" ".join(bits))))
    it = next((i for i in r["items"] if i["key"] == r["item"]["key"]), None)
    head = (f"{r['item']['name']} was already on the {r['list']['name']} list" if r.get("already")
            else f"Added {r['item']['name']} to the {r['list']['name']} list in KitchenOwl")  # fmt: skip
    errs = " Could not read: " + "; ".join(e for v in (r.get("errors") or {}).values() for e in v) + "." if r.get("errors") else ""
    return {"text": f"{head}. {_outdoor_text(it) if it else ''}{errs}".strip(), "data": r}


async def outdoors_check_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.outdoors.outdoors import check_now

    data = await _wrap(check_now(request, None))
    fell = data.get("fell") or []
    errs = " Could not read: " + "; ".join(e for v in (data.get("errors") or {}).values() for e in v) + "." if data.get("errors") else ""
    if not fell:
        return {"text": f"Checked {data['items']} items: no prices have fallen since the last check.{errs}", "data": data}
    return {"text": "Cheaper now: " + "; ".join(f"{p['name']} £{p['price']:.2f} (was £{p['from']:.2f})" for p in fell) + "." + errs,
            "data": data}  # fmt: skip


TOOLS: list[Tool] = [
    Tool("outdoors", "The family's Outdoors list in KitchenOwl, priced every day at GO Outdoors, Decathlon, Bikesy (with "
         "the shop each deal is at), Sigma Sports and Tradeinn: each thing's price now, the lowest seen, sale prices, "
         "stock, price drops this week, and the preferred local shop's price (Sigma Sports) even when dearer.",
         {"type": "object", "properties": _props(name=("string", "just this item (any part of its name)"))}, outdoors_tool),
    Tool("outdoors_add", "Add something to the Outdoors list in KitchenOwl (acts: adds it there) and price it now. Give "
         "a GO Outdoors or Decathlon product link to follow that product exactly; otherwise GO Outdoors is searched.",
         {"type": "object", "properties": _props(name=("string", "what you want, e.g. Two-man tent"),
                                                 link=("string", "a GO Outdoors or Decathlon product link"),
                                                 max_price=("number", "the most you would pay, £")),
          "required": ["name"]}, outdoors_add_tool),
    Tool("outdoors_check", "Price everything on the Outdoors list now and say what has got cheaper (it is also "
         "checked every morning).", {"type": "object", "properties": {}}, outdoors_check_tool),
]

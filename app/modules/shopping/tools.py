"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request

from app.assistant_base import Tool, ToolError, _need, _props, _wrap

LABEL = {"sainsburys": "Sainsbury's", "ocado": "Ocado"}


def _money(v: float) -> str:
    return f"£{v:.2f}"


def _retailer(args: dict[str, Any]) -> str:
    r = str(args.get("retailer") or "").strip().lower().replace("'", "").replace(" ", "")
    r = {"sainsbury": "sainsburys", "sainsburys": "sainsburys", "ocado": "ocado"}.get(r, r)
    if r not in LABEL:
        raise ToolError("Say which shop: sainsburys or ocado.")
    return r


def price_text(res: dict[str, Any]) -> str:
    t = res["totals"]
    bits = []
    for r in res["retailers"]:
        x = t["by_retailer"][r]
        bits.append(f"{x['label']} {_money(x['total'])} for {x['items']} item(s)" + (f", {x['missing']} not found" if x["missing"] else ""))
    out = "; ".join(bits) + "."
    if t.get("cheaper"):
        out += f" {LABEL[t['cheaper']]} is cheaper by {_money(t['saving'])} on the {t['compared_items']} item(s) both have."
    elif len(res["retailers"]) > 1 and t.get("compared_items"):
        out += " They come to the same on the items both have."
    return out


async def shopping_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.shopping.shopping import LAST_KEY, _get, current_items

    items = await _wrap(current_items(request))
    if str(args.get("about") or "list") == "prices":
        last = await _get(request, LAST_KEY)
        if not last:
            return {"text": "The list has not been priced yet: use price_list.", "data": {"items": items}}
        return {"text": f"Priced at {last['priced_at'][11:16]} UTC on {last['priced_at'][:10]}: " + price_text(last), "data": last}
    if not items:
        return {"text": "The shopping list is empty.", "data": {"items": []}}
    names = [i["name"] + (f" ({i['description']})" if i.get("description") else "") for i in items]
    return {"text": f"{len(items)} item(s) on the shopping list: " + ", ".join(names) + ".", "data": {"items": items}}


async def add_to_list_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.shopping.shopping import add_item

    raw = _need(args, "item")
    note = str(args.get("note") or "")
    # "milk, eggs and bread": one call, several items
    names = [n.strip() for n in re.split(r",|\band\b|\n", raw) if n.strip()] if args.get("split", True) else [raw]
    added, already = [], []
    for n in names[:20]:
        r = await _wrap(add_item(request, n, note if len(names) == 1 else ""))
        (added if r["added"] else already).append(r["item"]["name"])
    parts = []
    if added:
        parts.append("Added " + ", ".join(added) + " to the shopping list.")
    if already:
        parts.append(", ".join(already) + (" is" if len(already) == 1 else " are") + " already on it.")
    return {"text": " ".join(parts), "data": {"added": added, "already": already}}


async def price_list_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.shopping.shopping import PriceIn, price

    res = await _wrap(price(PriceIn(refresh=bool(args.get("fresh"))), request))
    return {"text": price_text(res), "data": {"priced_at": res["priced_at"], "totals": res["totals"]}}


async def send_to_basket_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.shopping.shopping import send_to_basket

    r = _retailer(args)
    out = await _wrap(send_to_basket(r, request))
    res = out.get("results", [])
    counts = {s: sum(1 for x in res if x.get("status") == s) for s in ("added", "raised", "already", "failed")}
    words = {"added": "added", "raised": "topped up", "already": "already there", "failed": "failed"}
    said = ", ".join(f"{n} {words[s]}" for s, n in counts.items() if n)
    tail = f" The {LABEL[r]} basket now has {out['basket']['count']} items, {_money(out['basket']['total'])}." if out.get("basket") else ""
    failed = [x.get("item") or x.get("name") for x in res if x.get("status") == "failed"]
    fail = (" Not added: " + ", ".join(failed) + ".") if failed else ""
    return {"text": f"{LABEL[r]} basket: {said}.{fail}{tail} Nothing is ordered: check out on their site or app.", "data": out}


def _order_line(o: dict[str, Any]) -> str:
    when = (o.get("slot_start") or "")[:10] or "no date"
    return f"{when}, {(o.get('status') or '').lower().replace('_', ' ')}, {_money(o.get('total') or 0)}"


async def orders_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.shopping.shopping import orders

    r = _retailer(args)
    d = await _wrap(orders(r, request))
    os_ = d.get("orders") or []
    if not os_:
        return {"text": f"No recent {LABEL[r]} orders" + (" delivered yet." if r == "ocado" else "."), "data": d}
    bits = []
    for o in os_[:3]:
        on = o.get("on_list") or []
        bits.append(_order_line(o) + (f", covers {len(on)} item(s) on the list ({', '.join(i['name'] for i in on[:6])})" if on else ""))
    return {"text": f"Recent {LABEL[r]} orders: " + "; ".join(bits) + ".", "data": d}


async def tick_off_order_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.shopping.shopping import KeysIn, orders, tick_off

    r = _retailer(args)
    d = await _wrap(orders(r, request))
    os_ = d.get("orders") or []
    want = str(args.get("order") or "")
    o = next((x for x in os_ if x["id"] == want), None) if want else (os_[0] if os_ else None)
    if o is None:
        raise ToolError(f"No {LABEL[r]} order " + (f"{want!r}" if want else "to tick off") + ". Try orders first.")
    keys = [i["key"] for i in o.get("on_list") or [] if args.get("include_name_matches", True) or i["how"] == "product"]
    if not keys:
        return {"text": f"Nothing on the list is in the {LABEL[r]} order of {_order_line(o)}.", "data": {"order": o}}
    res = await _wrap(tick_off(KeysIn(item_keys=keys), request))
    return {"text": f"Took {res['removed']} item(s) off the list, from the {LABEL[r]} order of {_order_line(o)}: "
                    + ", ".join(res["items"]) + ".", "data": {"order": o, **res}}  # fmt: skip


async def clear_list_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.shopping.shopping import ClearIn, clear_list

    if args.get("confirm") is not True:
        raise ToolError("Clearing the whole list needs confirm: true. Check with the person first.")
    res = await _wrap(clear_list(ClearIn(confirm=True), request))
    return {"text": f"Cleared the shopping list: {res['removed']} item(s) taken off. They stay in KitchenOwl's recent items.",
            "data": res}


TOOLS: list[Tool] = [
    Tool("shopping", "The family shopping list (KitchenOwl): what is on it, or what it came to at Sainsbury's and Ocado when last priced.",
         {"type": "object", "properties": _props(about=("string", "list (default) or prices"))}, shopping_tool),
    Tool("add_to_list", "Add items to the family shopping list (KitchenOwl). Several at once: 'milk, eggs and bread'.",
         {"type": "object", "properties": _props(item=("string", "what to add; commas or 'and' for several"),
                                                 note=("string", "optional note for a single item, e.g. 2 or semi-skimmed")),
          "required": ["item"]}, add_to_list_tool),
    Tool("price_list", "Price the shopping list now at Sainsbury's and Ocado: each total and which is cheaper. Takes up to a "
         "minute or two for a long list; searches are reused for six hours.",
         {"type": "object", "properties": _props(fresh=("boolean", "search again even if prices are under six hours old"))},
         price_list_tool),
    Tool("send_to_basket", "Put the last priced list's products in a shop's basket, each up to the quantity on the list "
         "(topped up, never doubled). Nothing is ordered or paid: you check out on their site or app.",
         {"type": "object", "properties": _props(retailer=("string", "sainsburys or ocado")), "required": ["retailer"]},
         send_to_basket_tool),
    Tool("orders", "Recent orders at a shop (Sainsbury's: upcoming ones too; Ocado: delivered ones only) and which items "
         "on the shopping list each covers.",
         {"type": "object", "properties": _props(retailer=("string", "sainsburys or ocado")), "required": ["retailer"]}, orders_tool),
    Tool("tick_off_order", "Take the items an order covers off the shopping list (the newest order, or the one given).",
         {"type": "object", "properties": _props(retailer=("string", "sainsburys or ocado"), order=("string", "order id; default: the newest"),
                                                 include_name_matches=("boolean", "also items matched only by name, default true")),
          "required": ["retailer"]}, tick_off_order_tool),
    Tool("clear_list", "Empty the whole shopping list. Only with confirm: true, after checking with the person.",
         {"type": "object", "properties": _props(confirm=("boolean", "must be true")), "required": ["confirm"]}, clear_list_tool),
]

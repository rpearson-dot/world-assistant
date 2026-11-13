"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, ToolError, _need, _props, _wrap


# --- The wishlist -----------------------------------------------------------------------------------------------
def _seen_text(s: dict[str, Any]) -> str:
    shop = {"aldi": "Aldi", "lidl": "Lidl"}.get(s["retailer"], s["retailer"])
    price = f" at £{s['price']:.2f}" if s.get("price") is not None else ""
    when = {"coming": f"in store from {s.get('on_sale')}", "on sale": "on sale now", "sold out": "sold out"}.get(
        s["status"], f"listed{' for ' + s['on_sale'] if s.get('on_sale') else ''}")  # fmt: skip
    return f"{s['name']} at {shop}{price}, {when}"


def _wish_text(it: dict[str, Any]) -> str:
    if it["now"]:
        return f"{it['name']}: " + "; ".join(_seen_text(s) for s in it["now"][:3]) + "."
    if it["last_seen"]:
        lo = it.get("last_on_sale")
        return (f"{it['name']}: not on sale now. Last seen {it['last_seen']['last_seen'][:10]}"
                + (f"; last on sale {lo['on_sale']}" + (f" at £{lo['price']:.2f}" if lo.get("price") is not None else "") if lo else "")
                + ".")  # fmt: skip
    return f"{it['name']}: not seen yet."


async def wishlist_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.wishlist.wishlist import wishes

    data = await _wrap(wishes(request))
    if not data["items"]:
        return {"text": f"The {data['list']['name']} list in KitchenOwl is empty. Add something with wish_add.", "data": data}
    want = str(args.get("name") or "").strip().lower()
    items = [i for i in data["items"] if not want or want in i["name"].lower()]
    if not items:
        return {"text": f"Nothing called {args.get('name')!r} on the wishlist.", "data": data}
    return {"text": " ".join(_wish_text(i) for i in items), "data": {**data, "items": items}}


async def wish_add_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.wishlist.wishlist import WishIn, add_wish, summary

    rules = []
    if args.get("max_price"):
        rules.append(f"under £{float(args['max_price']):g}")
    if args.get("must"):
        rules.append("must: " + ", ".join(str(m) for m in args["must"]))
    r = await _wrap(add_wish(request, WishIn(name=_need(args, "name"), description="; ".join(rules))))
    it = next((i for i in r["items"] if i["key"] == r["item"]["key"]), None)
    errs = f" ({'; '.join(r['errors'].values())})" if r.get("errors") else ""
    head = (f"{r['item']['name']} was already on the {r['list']['name']} list" if r.get("already")
            else f"Added {r['item']['name']} to the {r['list']['name']} list in KitchenOwl")  # fmt: skip
    return {"text": f"{head}, and checked Aldi and Lidl{errs}. {_wish_text(it or summary(r['item'], []))}", "data": r}


async def wish_remove_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.wishlist.wishlist import remove_wish, wishes

    name = _need(args, "name").lower()
    items = (await _wrap(wishes(request)))["items"]
    hit = [i for i in items if i["name"].lower() == name] or [i for i in items if name in i["name"].lower()]
    if len(hit) != 1:
        raise ToolError(f"Say which: {', '.join(i['name'] for i in hit)}" if hit else f"Nothing called {name!r} on the wishlist.")
    await _wrap(remove_wish(request, hit[0]["key"]))
    return {"text": f"Ticked {hit[0]['name']} off the wishlist in KitchenOwl.", "data": {"removed": hit[0]["name"]}}


async def wishlist_check_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.wishlist.wishlist import check_now

    data = await _wrap(check_now(request, None))
    errs = f" Could not read: {'; '.join(data['errors'].values())}." if data.get("errors") else ""
    found = [i for i in data["items"] if i["now"]]
    return {"text": (" ".join(_wish_text(i) for i in found) if found else
                     f"Checked {data['wishes']} wishes: none is coming or on sale now.") + errs, "data": data}  # fmt: skip


TOOLS: list[Tool] = [
    Tool("wishlist", "The family's Wishlist list in KitchenOwl: things to look out for in Aldi's Specialbuys and the "
         "Middle of Lidl. Says which "
         "are coming or on sale now (price, shop, date) and, for the rest, when each was last seen and last on sale.",
         {"type": "object", "properties": _props(name=("string", "just this wish (any part of its name)"))}, wishlist_tool),
    Tool("wish_add", "Add something to the family's Wishlist list in KitchenOwl (acts: adds it there) and check Aldi and "
         "Lidl for it straight away, e.g. Kamado BBQ. A product counts when its name has every word (bbq also finds "
         "barbecue and grill).",
         {"type": "object", "properties": {**_props(name=("string", "what you want, e.g. Kamado BBQ"),
                                                    max_price=("number", "only count it at or under this price, £")),
                                           "must": {"type": "array", "items": {"type": "string"},
                                                    "description": "words the product's name must have"}},
          "required": ["name"]}, wish_add_tool),
    Tool("wish_remove", "Tick something off the Wishlist list in KitchenOwl (acts: ticks it; its history is kept).",
         {"type": "object", "properties": _props(name=("string", "the wish, or part of its name")), "required": ["name"]},
         wish_remove_tool),
    Tool("wishlist_check", "Search Aldi and Lidl now for everything on the wishlist (it is also checked on Wednesday "
         "and Saturday evenings).", {"type": "object", "properties": {}}, wishlist_check_tool),
]

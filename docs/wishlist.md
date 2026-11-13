# Wishlist

A KitchenOwl list of things to look out for in **Aldi's Specialbuys** and the **Middle of Lidl**. Anyone in the family
adds a wish in the KitchenOwl app (or on the Wishlist page, or by asking the assistant); World Assistant searches Aldi
and Lidl for each one and remembers every time something matched, so it can say what is coming or on sale now and,
for the rest, when each was last seen and last on sale.

## Setting it up

1. In the KitchenOwl app, add a list called **Wishlist** in the same household as the shopping list (or choose
   another list on the Wishlist page). It must not be the shopping list.
2. Add things to it: "Kamado BBQ", "Ski jacket", "Air fryer".
3. Open **Wishlist** in World Assistant, or wait for the next check.

An item's description in KitchenOwl can narrow it:

| Description | Means |
| --- | --- |
| `under £300` (or `max £300`, `£300`) | only count it at or under £300 |
| `must: kamado` | a product's name must have "kamado" (instead of every word of the item's name) |
| anything else | left alone |

A product counts when its name has every word of the item (or the `must` words). Some words stand in for each other:
bbq, barbecue and grill; tv and television; hoover and vacuum; jumper, sweater and knit. Accessories (a cover, a bag, spares) do not count unless the item asks for one: "Kamado BBQ" skips a
kamado grill cover, "Kamado cover" finds it. Tick an item off in KitchenOwl
once it is bought: what was seen is kept, and comes back if the item is added again.

## When it checks

Wednesday and Saturday evening (about 18:40), before Aldi's new Specialbuys go on sale on Thursday and Sunday and
Lidl's on Thursday and Sunday too, and whenever you press **Check now** or add a wish. Each check is one search per
wish at each retailer, a second apart.

## What you see

- **Wishlist page:** each wish with what is coming (and from when) or on sale now, its price and a link; when it was
  last seen and last on sale; and every time it was seen.
- **Home Assistant:** `sensor.wa_wishlist_found` counts the wishes coming or on sale, with the details
  in its attributes, for an automation to tell you.
- **Assistant:** `wishlist`, `wish_add`, `wish_remove` (ticks it off in KitchenOwl) and `wishlist_check`.

"Last seen" can go back before the wishlist did: the retailers still list some past Specialbuys (sold out, with their
old on-sale date), and those count.

## How it works, and its limits

Neither Aldi nor Lidl has a public API. World Assistant reads the JSON searches their own websites use (Aldi's
in-store range, Lidl's whole range), gently and for personal use. They are unofficial, so when either changes its site
a check fails with a message saying so, and it needs fixing here. Nothing is ever bought or put in a basket.

To see exactly what each retailer sends:

    docker compose exec api python -m app.modules.wishlist.wishlist kamado bbq

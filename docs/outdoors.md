# Outdoors

A KitchenOwl list of outdoor gear, priced every morning at **GO Outdoors** and **Decathlon**, with price drops.
Anyone in the family adds to it in the KitchenOwl app (or on the Outdoors page, or by asking the assistant), and ticks
a thing off once it is bought.

## Setting it up

1. In the KitchenOwl app, add a list called **Outdoors** in the same household as the shopping list (or choose
   another list on the Outdoors page). It must not be the shopping list.
2. Add things to it. An item's description says what to follow:

| Description | Means |
| --- | --- |
| a GO Outdoors product link | that product exactly |
| a Sigma Sports product link | that product exactly |
| a Decathlon product link | that product exactly: Decathlon is only ever read this way (it does not allow its search to be read automatically) |
| nothing | GO Outdoors' search, Bikesy's deals from 30-odd UK bike shops, Sigma Sports' search, and Tradeinn's Trekkinn and Bikeinn shops, for the item's name; the three cheapest matches at each are followed |
| `under £300` | the most you would pay: the page marks what is under it |
| `must: gore-tex` | words a product's name has to have, instead of every word of the item's name |

Accessories (covers, bags, spares) are left out of a search unless the item asks for one.

## What you see

- **Outdoors page:** each thing with the products it follows: the price now, the shop's "was" price when it is on
  sale, the lowest price seen and when, stock, and **price drop** when it fell at its last check (shown for a week).
- **Home Assistant:** `sensor.wa_outdoors_price_drops` counts the drops this week, with which, from what and to what in
  its attributes, for an automation to tell you.
- **Assistant:** `outdoors`, `outdoors_add` and `outdoors_check`.

Every price change is kept, so each product's history is there too (`GET /v1/outdoors/{item}/history`).

## How it works, and its limits

GO Outdoors runs on Shopify, which publishes every shop's products as JSON at standard addresses; that is what is read.
**Bikesy** (bikesy.co.uk) compares 30-odd UK bike shops, which makes it good for bike parts. Its search pages are closed
to automated readers (robots.txt), so its tag pages are read instead (`/tag/<the item's words>/`, which are open): one
page per item a morning. Each deal links through Bikesy's "View Deal", which goes on to the shop; the shop is read from
the deal's logo or text when it is one of the shops known here, and shown as "Sigma Sports (via Bikesy)".

**Sigma Sports** (sigmasports.com; shops at Hampton Wick, Oakham, Letchworth and Wilmslow) has no API. A product link
is read from its page: the schema.org product data if it publishes it, else the page's heading and its price and "was"
price. Its search address is not published, so the likely ones are tried, only those its robots.txt allows, and the
first that lists products is used from then on; `WA_OUTDOORS_SIGMA_SEARCH` sets it (`{q}` for the words) and
`WA_OUTDOORS_SIGMA=false` stops the search (links are still read). Collect in store is chosen on its site.

**Preferred shops.** `WA_OUTDOORS_PREFERRED_SHOPS` (`Sigma Sports`) names shops whose price is shown beside the cheapest
even when dearer, with how much more, on the page and by the assistant: a local shop to collect from can be worth a few
pounds. A Bikesy deal at the shop counts.

**Tradeinn** (Trekkinn, Bikeinn and their sister shops, from Spain) has no public API; World Assistant asks the search its
own pages use, priced for delivery to the UK. Which shops is `WA_OUTDOORS_TRADEINN_SHOPS` (`trekkinn,bikeinn`; also
snowinn, runnerinn, waveinn, xtremeinn...). If it answers in euros, the prices are shown in euros and not compared with
the pound prices (for the cheapest, or "under £..."). Check delivery, VAT and returns before buying: orders come from
Spain.

**When a shop fails.** A shop that refuses, times out or answers with something unexpected is skipped for the rest of
that check (one failed request, not one per item), and the page says which shops were not reached. A followed product
that could not be read keeps its last price and says since when it could not be checked; it clears the next time it is
read.
For a Decathlon link, the product page is read for the product data it publishes for search engines. Both are read
once a morning (about 07:25), one request a second. GO Outdoors' member prices are not in what it publishes, so the
prices are the ones anyone pays. Nothing is ever bought.

To see exactly what each shop sends:

    docker compose exec api python -m app.outdoors two man tent
    docker compose exec api python -m app.outdoors https://www.decathlon.co.uk/p/...

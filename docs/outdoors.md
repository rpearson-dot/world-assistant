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
| a Decathlon product link | that product exactly: Decathlon is only ever read this way (it does not allow its search to be read automatically) |
| nothing | GO Outdoors' search for the item's name; its three cheapest matches are followed |
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
For a Decathlon link, the product page is read for the product data it publishes for search engines. Both are read
once a morning (about 07:25), one request a second. GO Outdoors' member prices are not in what it publishes, so the
prices are the ones anyone pays. Nothing is ever bought.

To see exactly what each shop sends:

    docker compose exec api python -m app.outdoors two man tent
    docker compose exec api python -m app.outdoors https://www.decathlon.co.uk/p/...

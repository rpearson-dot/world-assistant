# Shopping: the family list, priced at Sainsbury's and Ocado

The family keeps one shopping list in **KitchenOwl**, an open-source shopping list app (iPhone, Android and web)
that runs on your server. World Assistant reads that list, searches each item at Sainsbury's and Ocado, and shows
what the list costs at each, which is cheaper and by how much. It also shows the next delivery slots. It never books
a slot, fills a basket or buys anything: you order on the retailer's own site or app as usual.

Two optional parts, each a compose overlay:

| Overlay | What it adds |
| --- | --- |
| `docker-compose.kitchenowl.yml` | KitchenOwl ([TomBursch/kitchenowl](https://github.com/TomBursch/kitchenowl), v0.7.10), with its own Tailscale device so the family's phones reach it at home and away |
| `docker-compose.supermarket.yml` | The supermarket service (`services/supermarket`), built on [open-supermarkets](https://github.com/abracadabra50/open-supermarkets) 3.0.0, which searches Sainsbury's and Ocado |

The Shopping page (`/shopping`) brings them together. Nothing about shopping goes to Home Assistant.

**Unofficial, personal use.** Neither retailer offers a public API. open-supermarkets uses the same requests their
websites make, so it can stop working when they change their sites, and automated access may be against their terms.
World Assistant searches only when you press **Price this list**, keeps the results for six hours, and never polls.

## 1. KitchenOwl on your tailnet

KitchenOwl has no port on your LAN. It gets its own Tailscale device and `tailscale serve` puts it at
`https://kitchenowl.<your-tailnet>.ts.net` with a real certificate (Funnel stays off, so nothing is on the internet).
Every phone uses that one address, at home and away; the app keeps working offline and catches up when it reconnects.

1. In the [Tailscale admin console](https://login.tailscale.com/admin): MagicDNS and HTTPS Certificates on (already
   done if World Assistant is on Tailscale); **Settings > Keys > Generate auth key** (not reusable, not ephemeral).
   Note your tailnet's DNS name (**DNS** page, e.g. `tail1234.ts.net`).
2. On the server: `bash scripts/setup.sh`, answer **y** to KitchenOwl, give the tailnet name and the key. Or by
   hand: `WA_TAILNET=tail1234.ts.net` in `.env`, the key in `kitchenowl_tailscale_authkey.txt` in the secrets
   folder, `docker-compose.kitchenowl.yml` added to `COMPOSE_FILE`, then `bash scripts/deploy.sh`.
3. Open `https://kitchenowl.<your-tailnet>.ts.net` on a device on your tailnet. The first account you create is the
   admin. Create the household and a list called, say, "Shopping".

The list is kept in `WA_DATA_DIR/kitchenowl` (a SQLite database). World Assistant's own backup holds only its
database, so include that folder in your Unraid appdata backup (KitchenOwl can also export a household from its
settings).

### The family

Each person needs the KitchenOwl app and Tailscale on their phone. Two ways to let them reach KitchenOwl:

- **Share the device** (suggested). In the admin console, **Machines > kitchenowl > Share**, and send each person the
  link. They sign in to Tailscale with their own (free) account and accept. They see KitchenOwl and nothing else of
  yours, and they do not count as users of your tailnet.
- **Invite them to your tailnet** (Settings > Users), with an access rule that lets them reach only KitchenOwl on
  port 443. Check your plan's user limit first.

Then in the KitchenOwl app: choose your own server (**Other**, or **Switch server**) and give
`https://kitchenowl.<your-tailnet>.ts.net`, create an account, and add them to the household as members (household
settings in KitchenOwl).

Moving from Apple Reminders: there is no import, so type the current list into KitchenOwl once. Its suggestions
learn what you buy, so the next weeks are quicker.

## 2. World Assistant reads the list

1. In KitchenOwl: your account's settings, **Sessions**, then under *Long-lived tokens* **Create long-lived token**
   (name it "World Assistant"). Copy it: KitchenOwl shows it only once.
2. In World Assistant: **Shopping**, paste it under *The list* and **Save token**.
3. Choose the list and which retailers to compare, **Save**.

World Assistant reaches KitchenOwl inside the server (`http://kitchenowl:8080`), not through Tailscale. The token
has no expiry; delete it under Sessions in KitchenOwl to cut World Assistant off.

## 3. Prices: the supermarket service

1. `bash scripts/setup.sh` and answer **y** to the supermarket service (or add `docker-compose.supermarket.yml` to
   `COMPOSE_FILE` and `bash scripts/deploy.sh`). A release publishes its image next to World Assistant's
   (`ghcr.io/rpearson-dot/world-assistant-supermarket`); building from the folder builds it here.
2. **Shopping > Price this list.**

For each item World Assistant searches the item's name, plus its KitchenOwl description when that describes the
product ("semi-skimmed 4 pints"). A description that is a count ("2", "x3", "3 tins") is the quantity to buy and
multiplies the price. Each retailer's own top result that is in stock is the **best match**.

**Change** next to a product shows the other results; **Use this** makes that product **your usual** for that item
at that retailer, remembered from then on (by KitchenOwl item, so renaming the item keeps it). If your usual does not
turn up in a later search, the best match stands in and says so.

The totals: what each retailer's results come to, how many items each could not find, and a like-for-like total over
the items both have. The cheaper retailer is the lower like-for-like total. Prices are the shelf prices the search
shows; multi-buy offers, delivery charges and minimum spends are not included.

The first pricing of a long list takes a minute or two (two searches at a time at each retailer). Searches are
reused for six hours; **fresh prices** searches again.

Neither retailer needs a login to search, so prices work without one.

## 4. Delivery slots and logins

**Delivery slots** show the next free slots at each retailer, read only. Both need you logged in.

- **Ocado**: save the email and password under *Retailer logins*, then **Log in**. If Ocado refuses the login (it
  sits behind bot protection), use **Paste cookies instead**: log in on ocado.com in your own browser, export its
  cookies as JSON with a cookie export extension, and paste them.
- **Sainsbury's**: save the email and password, **Log in**, and Sainsbury's texts a code to your phone: type it in
  within five minutes. The login lasts about a week. Sainsbury's slots are read from its slot page in a browser,
  which takes 10 to 20 seconds; open-supermarkets reads that page by its layout, so this is the part most likely to
  stop working when Sainsbury's changes it.

The passwords are kept with World Assistant's other keys (the keys folder, or your vault) and only passed to the
supermarket service for a login. The logins themselves (cookies) are kept in `WA_DATA_DIR/supermarket`. **Log out**
deletes them.

## Troubleshooting

| Shopping page says | Do |
| --- | --- |
| KitchenOwl is not installed | Add `docker-compose.kitchenowl.yml` (above) |
| No KitchenOwl token yet / KitchenOwl refused the token | Make a new long-lived token in KitchenOwl and save it |
| KitchenOwl did not answer | `docker compose ps kitchenowl`, `docker compose logs --tail 40 kitchenowl` |
| The supermarket service is not set up / did not answer | Add `docker-compose.supermarket.yml`; `docker compose logs --tail 40 wa-supermarket` |
| A login fails | The error names a screenshot in `WA_DATA_DIR/supermarket/login-debug`: it shows what the retailer's page said (a captcha, a changed form, a wrong password). **Paste cookies instead** works whatever the form does |
| An item shows an error at one retailer | That search failed (often bot protection, briefly). Try again later with **fresh prices** |
| The phones cannot reach KitchenOwl | Tailscale on and connected; the device shared with them; `docker compose logs kitchenowl-tailscale` |

The supermarket service's API (only World Assistant calls it, with `supermarket_api_token`): `GET /health`,
`POST /compare`, `GET /slots?provider=`, `POST /login/start`, `POST /login/code`, `POST /session/import`,
`POST /logout`. See `services/supermarket/src/server.js`.

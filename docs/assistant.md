# Asking questions (MCP and Siri Shortcuts)

One set of tools answers short questions with a sentence that reads well aloud, plus the data
behind it. It is served two ways, with the same bearer token as the rest of the API.

Every feature with an API has a tool (a test makes sure a new one cannot be added without). A module that is
switched off hides its tools.

**Answers**

| Tool | Answers |
| --- | --- |
| `list_options` | which places, vehicles, trips, train boards and bus trackers exist (their ids) |
| `fuel_cheapest` | cheapest fuel near a saved place (`place`, optional `fuel`, `radius_km`, `brands`, `supermarkets`) |
| `events_near` | what is on near a saved place (`place`, optional `days`, `type` such as LIVE, COMEDY) |
| `pollen` | pollen levels and allergy risk near a saved place, today and the next three days |
| `electricity` | the price now and next, the next cheap and peak times, the cheapest block, Saving Sessions |
| `road_conditions` | works, closures, incidents and flood warnings on a saved trip's route |
| `trip_cost` | fuel or energy and cost for a saved routed trip in a saved vehicle (`trip`, `vehicle`) |
| `trains` | next trains on a saved board (`board`, optional `count`) |
| `station` | a TfL station's facilities and what is closed (lifts), or the stations near a place |
| `power` | UK Power Networks: power cuts near a place, past cuts, street works, what supplies it |
| `ev_chargers` | GRIDSERVE chargers near a place, along a trip or by name: working, free, speed, price |
| `bus` | whether a bus made the target stop before the deadline, and next buses (`tracker` optional if only one) |
| `floods_near` | flood warnings and alerts covering a place (`km` to look around it) |
| `road_disruptions_near` | TfL road works, closures and incidents near a place (`km`, default 0.5) |
| `here` | everything about a point that is not a saved place (lat and lon, or a postcode): buses, fuel, chargers, floods, air and water, pollen, events, roads, power cuts |
| `outdoors` | the KitchenOwl Outdoors list priced at GO Outdoors and Decathlon: prices, lowest seen, sales, drops this week |
| `outdoors_add` | add a thing to the Outdoors list, optionally with a product link, and price it (acts: adds it in KitchenOwl) |
| `outdoors_check` | price the Outdoors list now and say what got cheaper |
| `wishlist` | the family's KitchenOwl wishlist: what is coming or on sale at Aldi or Lidl, and when the rest were last seen |
| `wish_add` | add a wish to the KitchenOwl wishlist and check Aldi and Lidl for it (acts: adds it in KitchenOwl) |
| `wish_remove` | tick a wish off the KitchenOwl wishlist (acts: ticks it; its history is kept) |
| `wishlist_check` | search Aldi and Lidl now for everything on the wishlist |
| `bus_setup` | set up a bus tracker from loose stop names and an arrive-by time (acts: saves it; lists a line's stops when given only the line) |
| `live_buses_near` | buses reporting their position near a place now (`km`, `line`) |
| `bus_disruptions` | published bus disruptions near your places (`place`, `line`, `km`) |
| `stops_near` | the nearest stops and stations to a place (`km`, `kind`: bus_stop, rail, metro and so on) |
| `air_quality` | Defra's 1-10 air quality index near a place and the readings behind it |
| `river_levels` | river levels near a place (low, normal or high for there; rising or falling) and the rain |
| `storm_overflows` | storm overflows near a place and on the watched rivers: spilling now, in the last 48 hours, offline |
| `watch_rivers` | set the rivers whose storm overflows to watch (acts: saves a setting) |
| `tides` | the nearest tide gauge's measured level and trend (not a forecast) |
| `postcode` | where a postcode is (`postcode`), or the nearest postcode to a place |
| `connections` | how a place joins up in the knowledge graph, and what is disrupting the trips from it |
| `shopping` | what is on the family shopping list, or (`about: prices`) what it came to when last priced |
| `orders` | recent orders at a shop and which list items each covers (Sainsbury's: upcoming too; Ocado: delivered) |

**Acts.** These change something. None spends money.

| Tool | Does |
| --- | --- |
| `add_to_list` | adds items to the KitchenOwl list (`item`; "milk, eggs and bread" adds three; `note` for one item). What is already on it is left alone |
| `price_list` | prices the list now at Sainsbury's and Ocado and says which is cheaper (`fresh` to search again) |
| `send_to_basket` | puts the last priced list's products in a shop's basket (`retailer`), each up to the quantity on the list: topped up, never doubled. Nothing is ordered or paid; you check out on their site or app |
| `tick_off_order` | takes the items a shop's order covers off the list (`retailer`; the newest order, or `order`) |
| `clear_list` | empties the list; only with `confirm: true` |
| `add_place` | saves a place by `postcode` (or `lat` and `lon`); refuses to overwrite one unless `replace` |
| `add_trip` | saves a trip between two saved places and routes it |

Anyone with the API token can use these, through MCP or `/v1/ask`, so keep the token to people and assistants
you would let do them.

## Siri Shortcuts or any script

`POST /v1/ask/{tool}` with a JSON body of arguments and the header `Authorization: Bearer <token>`.
Add `?format=text` to get only the sentence.

In Shortcuts: *Get Contents of URL* (method POST, request body JSON, header `Authorization`), then
*Speak Text* with the result. For example `https://your-host/v1/ask/bus?format=text`. `GET /v1/ask`
lists the tools.

## MCP (for Claude and other MCP clients)

The endpoint is `POST /mcp` (MCP over HTTP, JSON responses, no sessions). Clients must be able to
send an `Authorization: Bearer` header.

* Claude Code: `claude mcp add --transport http world-assistant https://your-host/mcp --header "Authorization: Bearer <token>"`
* Other clients: use the client's remote or HTTP MCP setting, or a bridge such as `mcp-remote`, and
  supply the same header. Support for a fixed bearer header differs between clients; check yours.

## Reaching it from outside the house

Do not expose the port to the internet. Put the server on Tailscale and use its Tailscale name in
the URLs above; your phone then reaches it from anywhere while it is signed in to the same tailnet.
From the home network neither needs the token (see [security.md](security.md#access)); from anywhere else, including over Tailscale through a proxy, they do. The server itself only checks the token, so keep the token secret and rotate it if it leaks.
Remote connectors that are called from a vendor's cloud (rather than from your own device) cannot
reach a Tailscale-only address.

## Default place

Anywhere a saved place is needed (`/v1/fuel/near`, `/v1/events/near`, `/v1/pollen/near`, `/v1/pollen/history`
and the `fuel_cheapest`, `events_near` and `pollen` tools) it can be left out and your **home** is used: the
place of kind `home`, else the one with the id `home`, else the first by name. The `/fuel`, `/events` and
`/pollen` pages start on it too. Give a place to use another. `/v1/pollen/near` and `/v1/events/near` also take `lat` and `lon` (both, instead of a place) for a caller that knows its own location, such as Home Assistant; nothing is saved for those.

# Asking questions (MCP and Siri Shortcuts)

One set of tools answers short questions with a sentence that reads well aloud, plus the data
behind it. It is served two ways, with the same bearer token as the rest of the API.

| Tool | Answers |
| --- | --- |
| `list_options` | which places, vehicles, trips, train boards and bus trackers exist (their ids) |
| `fuel_cheapest` | cheapest fuel near a saved place (`place`, optional `fuel`, `radius_km`, `brands`, `supermarkets`; says how much more the chosen brands cost than the absolute cheapest) |
| `events_near` | what is on near a saved place (`place`, optional `days`, `type` such as LIVE, COMEDY) |
| `pollen` | pollen levels and allergy risk near a saved place, today and the next three days (`place`, optional) |
| `trip_cost` | fuel or energy and cost for a saved routed trip in a saved vehicle (`trip`, `vehicle`) |
| `trains` | next trains on a saved board (`board`, optional `count`) |
| `bus` | whether a bus made the target stop before the deadline, and next buses (`tracker` optional if only one) |

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

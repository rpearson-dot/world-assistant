# Skiddle, Ticketmaster and Data Thistle (`events`)

What is on near a place: gigs, comedy, theatre, sport, exhibitions.

Any one key is enough; Skiddle's is at skiddle.com/api, Data Thistle's at api.datathistle.com (save its refresh token too: World Assistant renews the 30-day access token with it).

How to use it: [docs/data-sources.md](../../../docs/data-sources.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | Skiddle; Ticketmaster; Data Thistle |
| Gives | Home Assistant: events near home |
| Keys | `skiddle_api_key`, `ticketmaster_api_key`, `datathistle_token`, `datathistle_refresh_token` |
| Endpoints | `/v1/events`, `/events` |
| Scheduled jobs | `events_sync`, `datathistle_renew` |
| Assistant tools | `events_near` |
| Plugs into | `assistant.tools`, `events.search` |

## Files

- `eventplace.py`: Event listings put in the wrong place: a provider's map position that disagrees with the venue's own postcode.
- `events.py`: What's on near a saved place: live Skiddle search, cached briefly. Nothing is stored.
- `sources/datathistle/client.py`: Data Thistle: the UK's live-events listings (theatre, comedy, gigs, exhibitions, family days out, festivals).
- `sources/localevents/sync.py`: What's on near your places, copied from Skiddle and Ticketmaster so it can be held, related and drawn.
- `sources/skiddle/client.py`: Skiddle events: what is on near a point.
- `sources/skiddle/probe.py`: Look at a real Skiddle response while setting up (needs the skiddle_api_key secret).
- `sources/ticketmaster/client.py`: Ticketmaster Discovery API: concerts, sport, theatre and shows near a point.
- `sources/ticketmaster/probe.py`: Look at a real Ticketmaster response while setting up (needs the ticketmaster_api_key secret).
- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

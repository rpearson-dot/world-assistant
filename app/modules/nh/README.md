# National Highways (`nh`)

Closures, speed limits and signs on motorways and major A roads.

How to use it: [docs/trip-conditions.md](../../../docs/trip-conditions.md).

| | |
|---|---|
| Keys | `nh_subscription_key`, `nh_subscription_key_secondary` |
| Scheduled jobs | `capture_job`, `poll_live`, `poll_planned` |
| Plugs into | `corridors.save`, `corridors.rematch` |

## Files

- `sources/nationalhighways/capture.py`: Temporary capture of raw National Highways responses into Postgres.
- `sources/nationalhighways/client.py`: Client for the National Highways data services (api.data.nationalhighways.co.uk).
- `sources/nationalhighways/corridors.py`: Corridor helpers and a command line for managing them.
- `sources/nationalhighways/match.py`: Match National Highways events and signs to corridors by geometry.
- `sources/nationalhighways/normalise.py`: Turn National Highways DATEX II payloads into plain, typed records.
- `sources/nationalhighways/poller.py`: Scheduled polling: fetch National Highways feeds, store, and tag by corridor.
- `sources/nationalhighways/store.py`: Postgres storage for National Highways events, signs and corridors.

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

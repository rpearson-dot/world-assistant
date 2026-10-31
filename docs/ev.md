# EV chargers (GRIDSERVE)

GRIDSERVE publishes its public chargers as open data (the Public Charge Point Regulations 2023 require it): every
site, each charger's live status, its connectors and power, and the price. World Assistant reads it every hour
(each read is the whole network, about 2 MB) and answers:

| What | Where |
|---|---|
| Sites nearest a place, with how many chargers work and are free now, by connector, the fastest and the price | `GET /v1/ev/sites?place=home&km=25`, the assistant, the map's "EV chargers" layer |
| Only sites with a connector, or with a free charger now | `&connector=chademo` (or `ccs`, `type2`), `&free=true` |
| A site by name, town or postcode, anywhere | `GET /v1/ev/sites?q=Rugby` |
| One site, every charger and its status | `GET /v1/ev/sites/gridserve:<id>` |
| Sites along a trip's route, in the order you pass them, and how far along | `GET /v1/ev/trips/<trip>?within_km=2` |
| Sites near your places and by your corridors, and the areas they are in | the knowledge graph (`ChargeSite`, preset "chargers") |

Ask the assistant: "GRIDSERVE chargers on the way to Mum's with CHAdeMO", "is there a free charger at Rugby?".

## Your car's connector

Set the rapid-charging connector on the Vehicles page (CCS, CHAdeMO or Type 2). The assistant then counts only
chargers your car can use ("for Leaf's CHAdeMO"); ask for any connector to see all. The API takes `vehicle=<id>` to
do the same, and `connector=any` for all.

## Open now and reliability

Each site's opening hours (OCPI regular hours and exceptional closings) give `open_now`, `closes` and `opens`; a site
that is closed says so ("closed now (opens Sun 05:30)") and is left out when only free chargers are asked for.
Every read records how many of each site's chargers were working, free and out of order (kept 400 days).
`/v1/ev/sites/{id}?days=30` and the assistant, asked about a site by name, say how often at least one charger was out
of order, how many on average and at worst.

## Home Assistant

Every site is published nationwide in map tiles (`sensor.wa_ev_tile_*`) with a summary (`sensor.wa_ev_summary`). The
Leaf dashboard's **Chargers near you** lists the nearest to whoever is looking (their phone while away, else home), for
the connector picked (the car's by default). The EV package (`world_assistant_ev.yaml`) adds the picker and
`sensor.wa_nearest_free_charger` for automations, measured from one person (`input_text.wa_ev_person`) or home.
See [home-assistant.md](home-assistant.md).

## Counting chargers

GRIDSERVE lists each lead as its own charger (an OCPI EVSE), numbered on the charger's id: `GB-GRD-EGS10343-1` (CCS),
`-2` (CHAdeMO) and `-3` (Type 2) are one box in the car park, which charges one car at a time on its rapid leads.
So World Assistant counts boxes: a charger is **in use** when any of its leads is, **free** when none is and one is
available, **working** when any lead can charge, and **out of order** when none can. By connector, a CHAdeMO lead on a
charger that is busy on CCS is not counted free. Sites not yet published (`publish: false`) and leads marked removed
or planned are left out.

Prices are per kWh with VAT (OCPI gives them without). A site listing several prices shows the range.

## Key

Request a key on GRIDSERVE's open data page (https://www.gridserve.com/open-data/) and save it as
`gridserve_api_key` (Settings > Keys). It is sent in an `ec-subscription-key` header, never in a URL.

## Checking it

    docker compose exec worker python -m app.integrations.gridserve.probe   # what the API sends and how it is read
    docker compose exec worker python -m app.integrations.gridserve.sync    # read it now

`WA_GRIDSERVE_POLL_ENABLED=false` turns it off; `WA_GRIDSERVE_INTERVAL_MIN` (60) sets how often it reads (5 to 60).
"Free now" is as of the last read: every answer says when that was (`updated`).
Each read replaces what is stored; an answer with fewer than half the stored sites is treated as a failed read and
changes nothing.

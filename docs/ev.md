# EV chargers (GRIDSERVE)

GRIDSERVE publishes its public chargers as open data (the Public Charge Point Regulations 2023 require it): every
site, each charger's live status, its connectors and power, and the price. World Assistant reads it every 10 minutes
and answers:

| What | Where |
|---|---|
| Sites nearest a place, with how many chargers work and are free now, by connector, the fastest and the price | `GET /v1/ev/sites?place=home&km=25`, the assistant, the map's "EV chargers" layer |
| Only sites with a connector, or with a free charger now | `&connector=chademo` (or `ccs`, `type2`), `&free=true` |
| A site by name, town or postcode, anywhere | `GET /v1/ev/sites?q=Rugby` |
| One site, every charger and its status | `GET /v1/ev/sites/gridserve:<id>` |
| Sites along a trip's route, in the order you pass them, and how far along | `GET /v1/ev/trips/<trip>?within_km=2` |
| Sites near your places and by your corridors, and the areas they are in | the knowledge graph (`ChargeSite`, preset "chargers") |

Ask the assistant: "GRIDSERVE chargers on the way to Mum's with CHAdeMO", "is there a free charger at Rugby?".

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

`WA_GRIDSERVE_POLL_ENABLED=false` turns it off; `WA_GRIDSERVE_INTERVAL_MIN` (10) sets how often it reads (5 to 60).
Each read replaces what is stored; an answer with fewer than half the stored sites is treated as a failed read and
changes nothing.

# Power (UK Power Networks)

UK Power Networks runs the local electricity network in London, the South East and the East of England. World
Assistant reads their open data portal (licence CC BY 4.0) for:

| What | How often | Where you see it |
|---|---|---|
| Power cuts, planned and unplanned | hourly; every 15 minutes while a cut is at home |  Home Assistant "Power cut at home", assistant, graph |
| Their street works, under way | every 2 hours | Trip conditions, assistant, graph |
| Their proposed street works | daily | Trip conditions (planned), assistant |
| What supplies each place: grid supply point, primary and local substation, rota block, independent operator | daily | `/v1/power/supply`, assistant, graph |
| Carbon intensity at the grid supply point feeding home | every 30 minutes | Energy tab, Home Assistant |

Nothing is read unless one of your saved places is on their network (the daily place check works that out).

## Key

Some datasets need a free account: register at https://ukpowernetworks.opendatasoft.com, make an API key under
your profile, and save it as `ukpn_api_key` (Settings > Keys).

## Checking it

    docker compose exec worker python -m app.integrations.ukpn.probe          # what the portal has, at home
    docker compose exec worker python -m app.integrations.ukpn.sync all       # read everything now

The probe lists each dataset found, its fields and how this app reads the first record. Datasets are found by their
published title and fields by their names, so a renamed field shows up there first.

## API

`GET /v1/power/cuts?place=home&km=5`, `/v1/power/works?place=home&km=1&stage=open|proposed`,
`/v1/power/supply?place=home`, `/v1/power/carbon?place=home&hours=3`.

Rota load disconnection blocks apply only in a national electricity supply emergency (the Electricity Supply
Emergency Code); the block tells you which published rota your home would follow.

## History

Every power cut seen is kept in `ukpn_fault_history`, with the saved places it was at. `GET /v1/power/history?place=home&days=365`
lists them: how long each lasted, how many homes, and whether power came back later than first promised.

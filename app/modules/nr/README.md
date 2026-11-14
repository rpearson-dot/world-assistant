# National Rail (`nr`)

Live departures from any station in Great Britain.

How to use it: [docs/trains.md](../../../docs/trains.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | National Rail Darwin (Rail Data Marketplace): departures |
| Gives | departures for the Trains page and Here |
| Keys | `nr_access_token` |

## Files

- `sources/rail/__init__.py`: Rail departures: the shared model and the National Rail client.
- `sources/rail/model.py`: The departure record shared by every rail source (TfL, National Rail).
- `sources/rail/probe.py`: Look at real National Rail responses while setting up.
- `sources/rail/rdm.py`: National Rail live departures from the Rail Data Marketplace (Darwin Live Departure Board).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

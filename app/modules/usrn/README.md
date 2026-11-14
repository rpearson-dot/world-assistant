# Street numbers (OS Open USRN) (`usrn`)

The number roadworks are reported against, with each street's line.

Large (a million and more streets), so opt in.

How to use it: [docs/boundaries.md](../../../docs/boundaries.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | OS Open USRN (a download) |
| Gives | street lines for road works |
| Uses when there | [boundary](../boundary/) |
| Scheduled jobs | `usrn_sync` |

## Files

- `sources/usrn/sync.py`: Open USRN: the street reference number and line of every street in Great Britain, from Ordnance Survey.

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

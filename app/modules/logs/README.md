# Logs (`logs`)

One place for the raw diagnostic feeds — the charger's own syslog, the OCPP protocol frames and the network event stream. Always available, so a module's logs can be read even after it has been switched off over a fault.

| | |
|---|---|
| Uses when there | [ocpp](../ocpp/) |
| Endpoints | `/v1/logs`, `/logs` |

## Files

- `__init__.py`: Logs: one place for the raw, diagnostic feeds that used to clutter the feature pages — the charger's own syslog, the OCPP protocol frames, and the network...
- `api.py`: The Logs API (/v1/logs): the raw diagnostic feeds, read straight from their tables. This module is core and never switched off, so the logs stay readable...
- `page.py`: The Logs pages: an overview and one page per raw feed (charger syslog, OCPP frames, network events). They hold no data and never build HTML from it;...

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._

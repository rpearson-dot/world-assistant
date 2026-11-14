# Documentation

New here? Read the [project README](../README.md) first: what World Assistant is for and what it does. Then
[architecture](architecture.md) shows how it is built.

## Running it

| Page | What it covers |
|---|---|
| [Runbook](runbook.md) | Installing, updating (staged, with no gap), the updater, troubleshooting |
| [Backups](backup.md) | Taking, downloading and restoring backups |
| [Tailscale](tailscale.md) | Reaching it from your phone anywhere, with HTTPS |
| [Notifications](notifications.md) | Alerts on your phone through ntfy |
| [Secrets and key vaults](secrets.md), [OpenBao](openbao.md) | Where keys and passwords live |
| [Security](security.md) | Who needs a token, what is protected, the supply chain |
| [Themes](themes.md) | The look of every page, light and dark |
| [Modules](modules.md) | Switching whole areas off |
| [Contributing a module](contributing-a-module.md) | Adding a new area: a pull request, or your own package |
| [Module SDK](module-sdk.md) | What a module may use from the core, and where it plugs in |
| [Moving to PostgreSQL 18](postgres-upgrade.md) | A one-time database upgrade for older installs |

## Each area

| Area | Pages |
|---|---|
| Getting about | [Bus tracker](bus.md), [bus route log](bus-log.md), [car charger (OCPP)](ocpp.md), [car (OVMS)](ovms.md), [air conditioning](airstage.md), [weather](weather.md), [live buses](live-buses.md), [bus disruptions](bus-disruptions.md), [trains](trains.md), [TfL stations](stations.md), [stops (NaPTAN)](naptan.md) |
| Roads and trips | [Trip conditions](trip-conditions.md), [TfL road disruptions](tfl-roads.md), [map](map.md), [Here](here.md) |
| Fuel, charging and vehicles | [EV chargers](ev.md), [Leaf dashboard](leaf.md) |
| Home | [Energy](energy.md), [power cuts](power.md) |
| Environment | [Air, water, tides and overflows](environment.md), [flood warnings](floods.md) |
| Shopping | [Shopping list](shopping.md), [wishlist](wishlist.md), [outdoors](outdoors.md) |
| Bikes | [Bike garage](bikes.md) |
| Places and reference data | [Postcodes](postcodes.md), [boundaries and streets](boundaries.md) |

## Connecting it to other things

| Page | What it covers |
|---|---|
| [Home Assistant](home-assistant.md) | Sensors over MQTT, dashboards, locations and routes from Home Assistant |
| [MQTT over TLS](mqtt-tls.md) | Certificates from a car's OVMS module through to Home Assistant |
| [Assistant](assistant.md) | Siri Shortcuts and AI assistants over MCP |
| [Knowledge graph](graph.md) | The optional Neo4j graph and the Graph page |
| [API reference](api.md) | Every endpoint, written from the code |

## How it is built

| Page | What it covers |
|---|---|
| [Architecture](architecture.md) | Containers, data flow, updates, code layout and database, with diagrams |
| [Data sources](data-sources.md) | Each feed: key, limits, licence and credit |
| [Database changes](migrations.md) | Why migrations only add, and how to remove something |
| [Decisions](adr/) | Why it is built the way it is |
| [GitHub settings](repo-settings.md) | Branch protection and the repository's settings |
| [Roadmap](roadmap.md) | Ideas parked for later |

Also: [changelog](../CHANGELOG.md), [contributing](../CONTRIBUTING.md), [security policy](../SECURITY.md).

"""The module SDK: what a module may use from the core, and where it can plug in. docs/module-sdk.md explains each;
tests/test_module_boundaries.py holds every module to it.

A module (app/modules/<id>/) may import:
  - the core modules listed in CORE below, and nothing else from the core;
  - its own folder;
  - the modules it declares in `needs` (cannot work without) or `uses` (uses when there, copes without).
The core never imports a module: it asks the registry for what modules provide at the points in POINTS.
"""

from __future__ import annotations

# The SDK's version. A module says which it was written for (Module(sdk=...)); one written for a newer SDK than this
# core offers is not loaded. Additions (a new extension point, a new Module field) raise it; nothing is taken away
# without a version that says so. 1: the first (0.52.0). 2: startup, Module.tabs, Module.occasional (0.53.0).
SDK_VERSION = 2

# The core's published interface, by module. Anything not listed is the core's own business and may change.
CORE: dict[str, str] = {
    "app.config": "settings (get_settings) and their defaults",
    "app.localnet": "private_peer, proxied: who connected (for endpoints only the home network may use)",
    "app.auth": "require_token, the dependency every /v1 router uses",
    "app.places": "saved places: place_or_default, the default place",
    "app.geo": "postcode lookup (postcodes.io, or a module's local table)",
    "app.geoquery": "distance SQL for PostGIS (POINT, within_sql, distance_km) and haversine_km",
    "app.corridor": "a route's corridor (centreline and buffer)",
    "app.ratelimit": "the shared rate limiter for outside services",
    "app.jobs": "recording a worker job's run (Settings shows when it last worked)",
    "app.secretstore": "reading a secret (keys)",
    "app.keys": "the Keys page's view of a secret",
    "app.notify": "phone notifications: send, sent, settings",
    "app.notify_jobs": "send_many and page_link, for several notifications in one message",
    "app.mqtt": "publish (and Message): send to the MQTT broker Home Assistant reads",
    "app.mqtt_config": "the MQTT broker as set in Settings (load) and a client for it (client), for a module that listens",
    "app.ha": "Home Assistant device and sensor shapes (Device, Sensor, Image)",
    "app.assistant_base": "Tool and its helpers, for assistant tools",
    "app.pagestyle": "the page shell (themed), menu and sub-tabs",
    "app.themes": "theme tokens",
    "app.whereami": "the shared place picker for pages (Where I am, then home)",
    "app.map_layers": "near_rows and point_feature, for a map layer of a module's own",
    "app.setup_checklist": "the Setup page's states (READY, WAITING, OFF...), for a setup check",
    "app.bankholidays": "UK bank holidays",
    "app.integrations": "shared clients: what3words, Ordnance Survey GeoPackage reading",
    "app.logging": "log formatting",
    "app.version": "the running version",
    "app.modules.model": "Module and Job, to declare a module",
    "app.modules.gate": "is_enabled: whether a module is switched on (for a websocket, which the gate does not cover)",
    "app.modules.registry": "import_string, and what other modules provide",
}

# Where a module can plug into the core: Module(provides={point: "app.modules.x.y:name"}).
POINTS: dict[str, str] = {
    "assistant.tools": "a list of Tool: the module's assistant tools (MCP and Siri)",
    "key.test": "async (settings) -> (ok, message): a live check that the module's key works",
    "setup.check": "async (settings) -> dict: a line on the Setup page's 'Your setup' list",
    "shutdown": "async () -> None: close the module's own connections when the api or worker stops",
    "startup": "async (app) -> None: start the module's long-lived work in the api (a charger's connection), once it is up",
    "ha.energy_device": "the Home Assistant energy device (the energy module)",
    "ha.energy_retired": "sensor ids the energy device no longer publishes",
    "ha.pollen_map": "(points) -> svg: the pollen map image for Home Assistant",
    "map.ev": "async (conn, centre, radius_km) -> features: the map's EV charger layer",
    "map.live_buses": "async (redis, lat, lon, radius_km) -> vehicles: the map's live buses layer",
    "fuel.canonical": "(text) -> fuel code or None: the map's fuel names",
    "naptan.station_types": "the stop types that count as stations on the national map",
    "bus.nearest_slot": "(slots, minutes) -> slot: a bus tracker's timetable slot, for Home Assistant",
    "postcodes.local": "async (engine, postcode) -> Located or None: a local postcode table",
    "corridors.save": "async (engine, corridor, source) -> None: keep a routed trip's corridor",
    "corridors.rematch": "async (engine) -> None: match stored events to every corridor again",
    "events.search": "async (request, name, lat, lon, ...) -> events: what is on near a point",
}

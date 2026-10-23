"""The modules that ship in the image. One declaration each; the core wires them (see model.py).

Adding a feature means adding an entry here (or shipping it as an outside package, see registry.discover) and
nothing else in the core: its routes, jobs, tools and Setup row come from the entry.
"""

from __future__ import annotations

from typing import Any

from app.modules.model import Job, Module


def _every(minutes: int) -> set[int]:
    return set(range(0, 60, max(1, minutes)))


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _live(s: Any) -> dict[str, Any]:
    m = _clamp(s.nh_live_interval_min, 1, 60)
    return {"minute": set(range(2, 60, m)), "timeout": 280, "max_tries": 1}


def _planned(s: Any) -> dict[str, Any]:
    h = _clamp(s.nh_planned_interval_hours, 1, 24)
    return {"hour": set(range(0, 24, h)), "minute": {50}, "timeout": 1500, "max_tries": 1}


def _fuel_prices(s: Any) -> dict[str, Any]:
    m = _clamp(s.fuel_price_interval_min, 5, 60)
    return {"minute": set(range(14, 60, m)), "timeout": 900, "max_tries": 1}


def _ha(s: Any) -> dict[str, Any]:
    m = _clamp(s.ha_interval_min, 1, 60)
    return {"minute": set(range(1, 60, m)), "timeout": 120, "max_tries": 1}


# The core: always on. Status, setup, keys, places, the assistant, Home Assistant, backups, updates, the pages
# that hold it together.
CORE = Module(
    "core", "Core", "Status, Setup, places, keys, backups, updates, Home Assistant and the assistant",
    category="service", core=True, listed=False,
    routers=(
        "app.status:router", "app.setup_checklist:router", "app.keys:router", "app.mqtt_config:router",
        "app.ha_smb:router", "app.ha_setup:router", "app.ha_setup:page_router", "app.autodeploy:router",
        "app.backups:router", "app.ha_locations:router", "app.ha_routes:router", "app.icons:router",
        "app.places:router", "app.map_layers:router", "app.map_page:page_router", "app.assistant:router",
        "app.places:page_router", "app.settings_page:page_router", "app.webapp:router",
        "app.modules.routes:router", "app.modules.routes:page_router",
    ),
    worker=(Job("app.ha_publish:ha_sync", _ha),),
    tools=("list_options",),
)

BUILTIN: tuple[Module, ...] = (
    CORE,
    Module("nh", "National Highways", "Closures, speed limits and signs on motorways and major A roads",
           category="transport", keys=("nh_subscription_key",), jobs=("nh_poll_live",), switch="nh_poll_enabled",
           link="https://developer.data.nationalhighways.co.uk/", coverage=("england",),
           worker=(
               Job("app.integrations.nationalhighways.capture:capture_job",
                   {"minute": {0, 20, 40}, "timeout": 1500, "max_tries": 1}),
               Job("app.integrations.nationalhighways.poller:poll_live", _live),
               Job("app.integrations.nationalhighways.poller:poll_planned", _planned),
           )),
    Module("fuel", "Fuel Finder (GOV.UK)", "Prices at every UK fuel station, cheapest near you or on the way",
           category="transport", keys=("fuel_finder_client_id", "fuel_finder_client_secret"),
           jobs=("fuel_prices", "fuel_stations"), switch="fuel_poll_enabled",
           link="https://www.gov.uk/guidance/access-the-latest-fuel-prices-and-forecourt-data-via-api-or-email",
           note="The API only answers UK addresses.",
           routers=("app.fuel:router", "app.fuel:page_router"), prefixes=("/v1/fuel", "/fuel"), tools=("fuel_cheapest",),
           worker=(
               Job("app.integrations.fuelfinder.sync:fuel_sync_prices", _fuel_prices),
               Job("app.integrations.fuelfinder.sync:fuel_sync_stations",
                   {"hour": {3}, "minute": {20}, "timeout": 900, "max_tries": 1}),
           )),
    Module("tfl", "Transport for London", "London bus times and the bus tracker, train boards, London road works",
           category="transport", keys=("tfl_app_key",), key_optional=True, jobs=("tflroads", "bus_poll"),
           link="https://api-portal.tfl.gov.uk/", note="Works without a key at a low rate.",
           routers=("app.tfl:router", "app.tfl:page_router"), prefixes=("/v1/tfl", "/trains"), tools=("trains",),
           coverage=("london",), area=(51.28, -0.52, 51.70, 0.34)),
    Module("nr", "National Rail", "Live departures from any station in Great Britain",
           category="transport", keys=("nr_access_token",), link="https://raildata.org.uk/"),
    Module("bods", "Bus Open Data Service", "Live bus positions anywhere in Britain; GPS for the bus tracker",
           category="transport", keys=("bods_api_key",), link="https://data.bus-data.dft.gov.uk/",
           routers=("app.bods:router",), prefixes=("/v1/live-buses",)),
    Module("tfwm", "Transport for West Midlands", "The region's own predicted bus and tram times (West Midlands only)",
           category="transport", keys=("tfwm_app_id", "tfwm_app_key"), link="https://api-portal.tfwm.org.uk/",
           note="Not used by the bus tracker yet: run the probe first.",
           coverage=("west midlands",), area=(52.30, -2.25, 52.75, -1.40)),
    Module("events", "Skiddle and Ticketmaster", "What is on near a place: gigs, comedy, theatre, sport",
           category="place", keys=("skiddle_api_key", "ticketmaster_api_key"), any_key=True,
           link="https://developer.ticketmaster.com/", note="Either key is enough; Skiddle's is at skiddle.com/api.",
           routers=("app.events:router", "app.events:page_router"), prefixes=("/v1/events", "/events"),
           tools=("events_near",),
           worker=(Job("app.integrations.localevents.sync:events_sync",
                       {"hour": {0, 6, 12, 18}, "minute": {12}, "timeout": 300, "max_tries": 1}),)),
    Module("pollen", "Polleninformation", "Pollen by allergen, today and three days ahead",
           category="place", keys=("pollen_api_key",), jobs=("pollen_grid_sync", "pollen_sync"),
           link="https://www.polleninformation.at/en/data-interface", note="Free for personal use; 40 requests a day.",
           routers=("app.pollen:router", "app.pollen:page_router"), prefixes=("/v1/pollen", "/pollen"), tools=("pollen",),
           worker=(
               Job("app.pollen_history:pollen_sync", {"hour": {4}, "minute": {25}, "timeout": 300, "max_tries": 1}),
               Job("app.pollen_grid:pollen_grid_sync", {"hour": {3, 15}, "minute": {40}, "timeout": 3000, "max_tries": 1}),
           )),
    Module("octopus", "Octopus Energy", "Your electricity rates, cheap windows and Saving Sessions",
           category="energy", keys=("octopus_api_key",), jobs=("energy",), switch="energy_poll_enabled",
           link="https://octopus.energy/dashboard/new/accounts/personal-details/api-access",
           note="Rates also work with just a tariff code (WA_OCTOPUS_TARIFF).", extra_ready=("octopus_tariff",),
           routers=("app.energy.routes:router", "app.energy.page:page_router"), prefixes=("/v1/energy", "/energy"),
           tools=("electricity",),
           worker=(Job("app.energy.sync:energy_sync", {"minute": {3, 33}, "timeout": 120, "max_tries": 1}),)),
    Module("floods", "Environment Agency", "Flood alerts and warnings in England, on the map and on your routes",
           category="data", jobs=("flood",), switch="flood_poll_enabled", coverage=("england",),
           routers=("app.floods:router",), prefixes=("/v1/floods",),
           worker=(Job("app.integrations.flood.sync:flood_poll",
                       {"minute": {5, 20, 35, 50}, "timeout": 240, "max_tries": 1}),)),
    Module("tflroads", "TfL road disruptions", "London works, closures and incidents near your routes",
           category="transport", jobs=("tflroads",), switch="tflroads_poll_enabled", coverage=("london",),
           area=(51.28, -0.52, 51.70, 0.34),
           routers=("app.tflroads:router",), prefixes=("/v1/roads/tfl",),
           worker=(Job("app.integrations.tflroads.sync:tflroads_poll",
                       {"minute": {7, 22, 37, 52}, "timeout": 120, "max_tries": 1}),)),
    Module("naptan", "Bus stops and stations (NaPTAN)", "Every stop and station in Great Britain",
           category="place", jobs=("naptan",), switch="naptan_poll_enabled", note="About 100 MB, loaded on first start.",
           routers=("app.stops:router",), prefixes=("/v1/stops",),
           worker=(Job("app.integrations.naptan.sync:naptan_sync",
                       {"hour": {2}, "minute": {50}, "timeout": 1800, "max_tries": 1}),)),
    Module("boundary", "Council and region boundaries (OS Boundary-Line)",
           "Which council, county and region a place, stop or roadwork is in",
           category="place", jobs=("boundary",), switch="boundary_poll_enabled",
           note="A large download, loaded on first start.",
           worker=(Job("app.integrations.boundaryline.sync:boundary_sync",
                       {"hour": {3}, "minute": {30}, "timeout": 3600, "max_tries": 1}),)),
    Module("usrn", "Street numbers (OS Open USRN)", "The number roadworks are reported against, with each street's line",
           category="place", jobs=("usrn",), switch="usrn_poll_enabled", note="Large (a million and more streets), so opt in.",
           worker=(Job("app.integrations.usrn.sync:usrn_sync",
                       {"hour": {4}, "minute": {5}, "timeout": 7200, "max_tries": 1}),)),
    Module("codepoint", "Postcodes (OS Code-Point Open)", "Places by postcode without an outside service",
           category="place", jobs=("codepoint",), switch="codepoint_poll_enabled", note="About 25 MB, loaded on first start.",
           routers=("app.postcodes:router",), prefixes=("/v1/postcodes",),
           worker=(Job("app.integrations.codepoint.sync:codepoint_sync",
                       {"hour": {3}, "minute": {10}, "timeout": 1800, "max_tries": 1}),)),
    # Features that are not a data source of their own: not on the Setup list of keys, but switchable here.
    Module("bus", "Bus tracker", "Has my bus made it, and the next buses at your stops",
           category="transport", listed=False, jobs=("bus_poll",),
           routers=("app.bus:router", "app.bus_page:page_router"), prefixes=("/v1/bus", "/bus"), tools=("bus",),
           worker=(Job("app.integrations.bus.poller:poll_bus",
                       {"hour": {*range(5, 11), *range(14, 19)}, "second": {0, 30}, "timeout": 25, "max_tries": 1}),)),
    Module("conditions", "Trip conditions", "Road works, closures and floods on a saved trip's route",
           category="transport", listed=False,
           routers=("app.conditions:router",), prefixes=("/v1/conditions", "re:/v1/trips/[^/]+/conditions"), tools=("road_conditions",)),
    Module("vehicles", "Vehicles and trip cost", "What a trip costs in fuel or electricity for a saved vehicle",
           category="energy", listed=False,
           routers=("app.vehicles:router", "app.vehicles_page:page_router"),
           prefixes=("/v1/vehicles", "/vehicles", "re:/v1/trips/[^/]+/cost"), tools=("trip_cost",)),
    Module("graph", "Knowledge graph (Neo4j)", "The Graph page: how your places, trips, roads and stations connect",
           category="service", listed=False, switch="graph_enabled", services=("docker-compose.graph.yml",),
           routers=("app.graph_routes:router", "app.graph_routes:page_router"), prefixes=("/v1/graph", "/graph"),
           worker=(Job("app.graph.build:graph_build", {"minute": {8, 38}, "timeout": 300, "max_tries": 1}),)),
    Module("routing", "Routing (Valhalla)", "Real road routes for trips, so road events, fuel and conditions follow them",
           category="service", listed=False, services=("docker-compose.routing.yml",)),
)

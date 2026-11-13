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


def _gridserve(s: Any) -> dict[str, Any]:
    m = _clamp(s.gridserve_interval_min, 5, 60)
    return {"minute": set(range(6, 60, m)), "timeout": 240, "max_tries": 1}


def _ha(s: Any) -> dict[str, Any]:
    m = _clamp(s.ha_interval_min, 1, 60)
    return {"minute": set(range(1, 60, m)), "timeout": 120, "max_tries": 1}


# The core: always on. Status, setup, keys, places, the assistant, Home Assistant, backups, updates, the pages
# that hold it together.
CORE = Module(
    "core", "Core", "Status, Setup, places, keys, backups, updates, Home Assistant and the assistant",
    category="service", core=True, listed=False,
    routers=(
        "app.status:router", "app.setup_checklist:router", "app.keys:router", "app.look:router", "app.mqtt_config:router",
        "app.ha_smb:router", "app.ha_setup:router", "app.ha_setup:page_router", "app.ha_discover:router",
        "app.autodeploy:router",
        "app.backups:router", "app.ha_locations:router", "app.ha_routes:router", "app.icons:router",
        "app.places:router", "app.map_layers:router", "app.map_page:page_router", "app.assistant:router",
        "app.places:page_router", "app.settings_page:page_router", "app.webapp:router",
        "app.modules.routes:router", "app.modules.routes:page_router", "app.notify:router",
    ),
    worker=(Job("app.ha_publish:ha_sync", _ha),),
    tools=("list_options", "add_place", "add_trip"),
)

BUILTIN: tuple[Module, ...] = (
    CORE,
    Module("nh", "National Highways", "Closures, speed limits and signs on motorways and major A roads",
           category="transport", keys=("nh_subscription_key",), more_keys=("nh_subscription_key_secondary",), jobs=("nh_poll_live",), switch="nh_poll_enabled",
           link="https://developer.data.nationalhighways.co.uk/", coverage=("england",),
           worker=(
               Job("app.modules.nh.sources.nationalhighways.capture:capture_job",
                   {"minute": {0, 20, 40}, "timeout": 1500, "max_tries": 1}),
               Job("app.modules.nh.sources.nationalhighways.poller:poll_live", _live),
               Job("app.modules.nh.sources.nationalhighways.poller:poll_planned", _planned),
           ),
           provides={"corridors.save": "app.modules.nh.sources.nationalhighways.store:upsert_corridor", "corridors.rematch": "app.modules.nh.sources.nationalhighways.store:rematch_all"}),
    Module("fuel", "Fuel Finder (GOV.UK)", "Prices at every UK fuel station, cheapest near you or on the way",
           category="transport", keys=("fuel_finder_client_id", "fuel_finder_client_secret"),
           jobs=("fuel_prices", "fuel_stations"), switch="fuel_poll_enabled",
           link="https://www.gov.uk/guidance/access-the-latest-fuel-prices-and-forecourt-data-via-api-or-email",
           note="The API only answers UK addresses.",
           routers=("app.modules.fuel.fuel:router", "app.modules.fuel.fuel:page_router"), prefixes=("/v1/fuel", "/fuel"), tools=("fuel_cheapest",),
           worker=(
               Job("app.modules.fuel.sources.fuelfinder.sync:fuel_sync_prices", _fuel_prices),
               Job("app.modules.fuel.sources.fuelfinder.sync:fuel_sync_stations",
                   {"hour": {3}, "minute": {20}, "timeout": 900, "max_tries": 1}),
           ),
           provides={"assistant.tools": "app.modules.fuel.tools:TOOLS", "fuel.canonical": "app.modules.fuel.sources.fuelfinder.normalise:canonical_fuel"},
           uses=("nh", "vehicles")),
    Module("tfl", "Transport for London", "London bus times and the bus tracker, train boards, London road works",
           category="transport", keys=("tfl_app_key",), key_optional=True, jobs=("tflroads", "bus_poll"),
           link="https://api-portal.tfl.gov.uk/", note="Works without a key at a low rate.",
           routers=("app.modules.tfl.tfl:router", "app.modules.tfl.tfl:page_router"), prefixes=("/v1/tfl", "/trains"), tools=("trains",),
           coverage=("london",), area=(51.28, -0.52, 51.70, 0.34),
           provides={"assistant.tools": "app.modules.tfl.tools:TOOLS", "key.test": "app.modules.tfl.keytest:test_key"},
           uses=("nr",)),
    Module("nr", "National Rail", "Live departures from any station in Great Britain",
           category="transport", keys=("nr_access_token",), link="https://raildata.org.uk/"),
    Module("bods", "Bus Open Data Service", "Live bus positions anywhere in Britain; GPS for the bus tracker",
           category="transport", keys=("bods_api_key",), link="https://data.bus-data.dft.gov.uk/",
           routers=("app.modules.bods.bods:router",), prefixes=("/v1/live-buses",), tools=("live_buses_near",),
           provides={"assistant.tools": "app.modules.bods.tools:TOOLS", "map.live_buses": "app.modules.bods.bods:vehicles_near", "key.test": "app.modules.bods.keytest:test_key"},
           uses=("bus", "naptan", "tfl")),
    Module("tfwm", "Transport for West Midlands", "The region's own predicted bus and tram times (West Midlands only)",
           category="transport", keys=("tfwm_app_id", "tfwm_app_key"), link="https://api-portal.tfwm.org.uk/",
           note="Not used by the bus tracker yet: run the probe first.",
           coverage=("west midlands",), area=(52.30, -2.25, 52.75, -1.40),
           provides={"key.test": "app.modules.tfwm.keytest:test_key"},
           uses=("bods", "bus")),
    Module("events", "Skiddle, Ticketmaster and Data Thistle", "What is on near a place: gigs, comedy, theatre, sport, exhibitions",
           category="place", keys=("skiddle_api_key", "ticketmaster_api_key", "datathistle_token"), any_key=True,
           more_keys=("datathistle_refresh_token",),
           link="https://developer.ticketmaster.com/",
           note="Any one key is enough; Skiddle's is at skiddle.com/api, Data Thistle's at api.datathistle.com (save its "
                "refresh token too: World Assistant renews the 30-day access token with it).",
           routers=("app.modules.events.events:router", "app.modules.events.events:page_router"), prefixes=("/v1/events", "/events"),
           tools=("events_near",),
           worker=(Job("app.modules.events.sources.localevents.sync:events_sync",
                       {"hour": {0, 6, 12, 18}, "minute": {12}, "timeout": 900, "max_tries": 1}),
                   Job("app.modules.events.sources.datathistle.client:datathistle_renew",
                       {"hour": {4}, "minute": {17}, "timeout": 120, "max_tries": 1}),),
           provides={"assistant.tools": "app.modules.events.tools:TOOLS", "events.search": "app.modules.events.events:search"}),
    Module("pollen", "Polleninformation", "Pollen by allergen, today and three days ahead",
           category="place", keys=("pollen_api_key",), jobs=("pollen_grid_sync", "pollen_sync"),
           link="https://www.polleninformation.at/en/data-interface", note="Free for personal use; 40 requests a day.",
           routers=("app.modules.pollen.pollen:router", "app.modules.pollen.pollen:page_router"), prefixes=("/v1/pollen", "/pollen"), tools=("pollen",),
           worker=(
               Job("app.modules.pollen.pollen_history:pollen_sync", {"hour": {4}, "minute": {25}, "timeout": 300, "max_tries": 1}),
               Job("app.modules.pollen.pollen_grid:pollen_grid_sync", {"hour": {3, 15}, "minute": {40}, "timeout": 3000, "max_tries": 1}),
           ),
           provides={"assistant.tools": "app.modules.pollen.tools:TOOLS", "ha.pollen_map": "app.modules.pollen.pollen_map:render_svg"}),
    Module("octopus", "Octopus Energy", "Your electricity rates, cheap windows and Saving Sessions",
           category="energy", keys=("octopus_api_key",), jobs=("energy",), switch="energy_poll_enabled",
           link="https://octopus.energy/dashboard/new/accounts/personal-details/api-access",
           note="Rates also work with just a tariff code (WA_OCTOPUS_TARIFF).", extra_ready=("octopus_tariff",),
           routers=("app.modules.octopus.routes:router", "app.modules.octopus.page:page_router"), prefixes=("/v1/energy", "/energy"),
           tools=("electricity",),
           worker=(Job("app.modules.octopus.sync:energy_sync", {"minute": {3, 33}, "timeout": 120, "max_tries": 1}),),
           provides={"assistant.tools": "app.modules.octopus.tools:TOOLS", "ha.energy_device": "app.modules.octopus.ha:energy_device", "ha.energy_retired": "app.modules.octopus.ha:RETIRED"}),
    Module("floods", "Environment Agency", "Flood alerts and warnings in England, on the map and on your routes",
           category="data", jobs=("flood",), switch="flood_poll_enabled", coverage=("england",),
           routers=("app.modules.floods.floods:router",), prefixes=("/v1/floods",),
           worker=(Job("app.modules.floods.sources.flood.sync:flood_poll",
                       {"minute": {5, 20, 35, 50}, "timeout": 240, "max_tries": 1}),), tools=("floods_near",),
           provides={"assistant.tools": "app.modules.floods.tools:TOOLS"}),
    Module("environment", "Air and water", "Air quality (Defra's 1-10 index), river levels, rainfall and tide gauges near your places",
           category="place", link="https://uk-air.defra.gov.uk/data/about_sos",
           note="Live from Defra UK-AIR and the Environment Agency; no key. Rivers, rain and tides: England only.",
           routers=("app.modules.environment.environment:router", "app.modules.environment.environment_page:page_router"), prefixes=("/v1/environment", "/environment"),
           worker=(Job("app.modules.environment.notify:environment_alerts", {"minute": {9, 39}, "timeout": 300, "max_tries": 1}),),
           tools=("air_quality", "river_levels", "tides", "storm_overflows", "watch_rivers"),
           provides={"assistant.tools": "app.modules.environment.tools:TOOLS"},
           uses=("pollen",)),
    Module("tflroads", "TfL road disruptions", "London works, closures and incidents near your routes",
           category="transport", jobs=("tflroads",), switch="tflroads_poll_enabled", coverage=("london",),
           area=(51.28, -0.52, 51.70, 0.34),
           routers=("app.modules.tflroads.tflroads:router",), prefixes=("/v1/roads/tfl",),
           worker=(Job("app.modules.tflroads.sources.tflroads.sync:tflroads_poll",
                       {"minute": {7, 22, 37, 52}, "timeout": 120, "max_tries": 1}),), tools=("road_disruptions_near",),
           provides={"assistant.tools": "app.modules.tflroads.tools:TOOLS"},
           uses=("tfl",)),
    Module("tflstations", "TfL stations: layout, facilities and lifts",
           "Entrances, platforms, lines, toilets and step-free access at every TfL station, and lifts out of service now",
           category="transport", keys=("tfl_app_key",), key_optional=True, jobs=("tfl_stations", "tfl_station_alerts"),
           link="https://api-portal.tfl.gov.uk/",
           switch="tfl_stations_poll_enabled", coverage=("london",), area=(51.28, -0.52, 51.70, 0.34),
           note="Underground, Overground, DLR, Elizabeth line and tram; National Rail-only stations are not in TfL's data.",
           routers=("app.modules.tflstations.stations:router",), prefixes=("/v1/stations",), tools=("station",),
           worker=(Job("app.modules.tflstations.sources.tflstations.sync:tfl_stations_sync",
                       {"hour": {3}, "minute": {40}, "timeout": 600, "max_tries": 2}),
                   Job("app.modules.tflstations.sources.tflstations.sync:tfl_station_alerts",
                       {"minute": {4, 14, 24, 34, 44, 54}, "timeout": 120, "max_tries": 1})),
           provides={"assistant.tools": "app.modules.tflstations.tools:TOOLS"},
           uses=("tfl",)),
    Module("ukpn", "UK Power Networks", "Power cuts and electricity street works near your places, and the substations feeding them "
           "(London, South East and East of England)",
           category="energy", keys=("ukpn_api_key",), key_optional=True, switch="ukpn_poll_enabled",
           jobs=("ukpn_places", "ukpn_faults", "ukpn_works", "ukpn_proposed"),
           link="https://ukpowernetworks.opendatasoft.com/", note="Reads nothing unless a saved place is on their network. CC BY 4.0.",
           coverage=("london", "south east", "east of england"), area=(50.75, -1.30, 53.05, 1.80),
           routers=("app.modules.ukpn.power:router",), prefixes=("/v1/power",), tools=("power",),
           worker=(Job("app.modules.ukpn.sources.ukpn.sync:ukpn_places", {"hour": {4}, "minute": {35}, "timeout": 900, "max_tries": 2}),
                   Job("app.modules.ukpn.sources.ukpn.sync:ukpn_faults", {"minute": {2, 17, 32, 47}, "timeout": 120, "max_tries": 1}),
                   Job("app.modules.ukpn.sources.ukpn.sync:ukpn_works_open", {"hour": set(range(0, 24, 2)), "minute": {12},
                                                                       "timeout": 600, "max_tries": 1}),
                   Job("app.modules.ukpn.sources.ukpn.sync:ukpn_works_proposed", {"hour": {4}, "minute": {50}, "timeout": 900, "max_tries": 2})),
           provides={"assistant.tools": "app.modules.ukpn.tools:TOOLS"}),
    Module("gridserve", "GRIDSERVE EV chargers", "GRIDSERVE's chargers near your places and along your trips: how many "
           "work and are free now, by connector, and the price",
           category="transport", keys=("gridserve_api_key",), jobs=("gridserve",), switch="gridserve_poll_enabled",
           link="https://www.gridserve.com/open-data/", note="Open data under the Public Charge Point Regulations 2023.",
           routers=("app.modules.gridserve.ev:router",), prefixes=("/v1/ev",), tools=("ev_chargers",),
           worker=(Job("app.modules.gridserve.sources.gridserve.sync:gridserve_sync", _gridserve),),
           provides={"assistant.tools": "app.modules.gridserve.tools:TOOLS", "map.ev": "app.modules.gridserve.map_layer:ev_features"},
           uses=("conditions",)),
    Module("busdisruptions", "Bus disruptions (BODS)",
           "Bus, tram and ferry diversions and closures near your places, where the local authority publishes them",
           category="transport", keys=("bods_api_key",), jobs=("bus_disruptions",), switch="bus_disruptions_poll_enabled",
           needs=("bods",), link="https://data.bus-data.dft.gov.uk/",
           note="Only some authorities publish (none in London or the Midlands yet); off unless WA_BUS_DISRUPTIONS_POLL_ENABLED=true.",
           # the publishers seen in October 2026; where you live decides whether it is suggested
           coverage=("north west", "south west", "yorkshire", "hampshire", "telford", "bedford", "luton",
                     "cheshire east", "north lincolnshire"),
           routers=("app.modules.busdisruptions.bus_disruptions:router",), prefixes=("/v1/bus-disruptions",),
           worker=(Job("app.modules.busdisruptions.disruptions_sync:bus_disruptions_poll",
                       {"minute": {1, 11, 21, 31, 41, 51}, "timeout": 180, "max_tries": 1}),), tools=("bus_disruptions",),
           provides={"assistant.tools": "app.modules.busdisruptions.tools:TOOLS"},
           uses=("bus",)),
    Module("naptan", "Bus stops and stations (NaPTAN)", "Every stop and station in Great Britain",
           category="place", jobs=("naptan",), switch="naptan_poll_enabled", note="About 100 MB, loaded on first start.",
           routers=("app.modules.naptan.stops:router",), prefixes=("/v1/stops",),
           worker=(Job("app.modules.naptan.sources.naptan.sync:naptan_sync",
                       {"hour": {2}, "minute": {50}, "timeout": 1800, "max_tries": 1}),), tools=("stops_near",),
           provides={"assistant.tools": "app.modules.naptan.tools:TOOLS", "naptan.station_types": "app.modules.naptan.sources.naptan.normalise:STATION_TYPES"}),
    Module("boundary", "Council and region boundaries (OS Boundary-Line)",
           "Which council, county and region a place, stop or roadwork is in",
           category="place", jobs=("boundary",), switch="boundary_poll_enabled",
           note="A large download, loaded on first start.",
           worker=(Job("app.modules.boundary.sources.boundaryline.sync:boundary_sync",
                       {"hour": {3}, "minute": {30}, "timeout": 3600, "max_tries": 1}),)),
    Module("usrn", "Street numbers (OS Open USRN)", "The number roadworks are reported against, with each street's line",
           category="place", jobs=("usrn",), switch="usrn_poll_enabled", note="Large (a million and more streets), so opt in.",
           worker=(Job("app.modules.usrn.sources.usrn.sync:usrn_sync",
                       {"hour": {4}, "minute": {5}, "timeout": 7200, "max_tries": 1}),),
           uses=("boundary",)),
    Module("codepoint", "Postcodes (OS Code-Point Open)", "Places by postcode without an outside service",
           category="place", jobs=("codepoint",), switch="codepoint_poll_enabled", note="About 25 MB, loaded on first start.",
           routers=("app.modules.codepoint.postcodes:router",), prefixes=("/v1/postcodes",), tools=("postcode",),
           worker=(Job("app.modules.codepoint.sources.codepoint.sync:codepoint_sync",
                       {"hour": {3}, "minute": {10}, "timeout": 1800, "max_tries": 1}),),
           provides={"assistant.tools": "app.modules.codepoint.tools:TOOLS", "postcodes.local": "app.modules.codepoint.postcodes:local_postcode"}),
    # Features that are not a data source of their own: not on the Setup list of keys, but switchable here.
    Module("bus", "Bus tracker", "Has my bus made it, and the next buses at your stops",
           category="transport", listed=False, jobs=("bus_poll",),
           routers=("app.modules.bus.bus:router", "app.modules.bus.bus_page:page_router"), prefixes=("/v1/bus", "/bus"), tools=("bus", "bus_setup"),
           worker=(Job("app.modules.bus.sources.bus.poller:poll_bus",
                       {"hour": {*range(5, 11), *range(14, 19)}, "second": {0, 30}, "timeout": 25, "max_tries": 1}),),
           provides={"assistant.tools": "app.modules.bus.tools:TOOLS", "bus.nearest_slot": "app.modules.bus.sources.bus.events:nearest_slot"},
           uses=("bods", "tfl")),
    Module("conditions", "Trip conditions", "Road works, closures and floods on a saved trip's route",
           category="transport", listed=False,
           routers=("app.modules.conditions.conditions:router",), prefixes=("/v1/conditions", "re:/v1/trips/[^/]+/conditions"), tools=("road_conditions",),
           provides={"assistant.tools": "app.modules.conditions.tools:TOOLS"},
           uses=("tflroads",)),
    Module("vehicles", "Vehicles and trip cost", "What a trip costs in fuel or electricity for a saved vehicle",
           category="energy", listed=False,
           routers=("app.modules.vehicles.vehicles:router", "app.modules.vehicles.vehicles_page:page_router"),
           prefixes=("/v1/vehicles", "/vehicles", "re:/v1/trips/[^/]+/cost"), tools=("trip_cost",),
           provides={"assistant.tools": "app.modules.vehicles.tools:TOOLS"},
           uses=("fuel", "gridserve", "nh", "octopus")),
    Module("vehiclechecks", "Tax and MOT", "Each vehicle's tax and MOT status and due dates, its MOT history with "
           "advisories, and outstanding safety recalls (DVLA and DVSA), with reminders", category="energy",
           keys=("dvla_api_key", "dvsa_mot_api_key"), any_key=True,
           more_keys=("dvsa_mot_client_id", "dvsa_mot_client_secret", "dvsa_mot_token_url"), jobs=("vehicle_check",),
           link="https://developer-portal.driver-vehicle-licensing.api.gov.uk/",
           note="DVLA: apply for a Vehicle Enquiry Service key. DVSA: register at documentation.history.mot.api.gov.uk; "
                "its email gives the API key, client id and secret, and the token URL. Either works alone. Give each "
                "vehicle its registration on the Vehicles page.",
           routers=("app.modules.vehiclechecks.vehiclecheck:router",), prefixes=("/v1/vehicle-checks",),
           worker=(Job("app.modules.vehiclechecks.vehiclecheck:vehicle_check", {"weekday": {0}, "hour": {7}, "minute": {5}, "timeout": 300,
                                                          "max_tries": 1}),),
           tools=("vehicle_checks",),
           provides={"assistant.tools": "app.modules.vehiclechecks.tools:TOOLS"}),
    Module("shopping", "Shopping", "The family's KitchenOwl list, priced at Sainsbury's and Ocado",
           category="home", keys=("kitchenowl_token",), link="https://github.com/TomBursch/kitchenowl",
           note="Needs docker-compose.kitchenowl.yml, and docker-compose.supermarket.yml for prices (docs/shopping.md)",
           services=("docker-compose.kitchenowl.yml", "docker-compose.supermarket.yml"),
           routers=("app.modules.shopping.shopping:router", "app.modules.shopping.shopping_page:page_router"), prefixes=("/v1/shopping", "/shopping"), tools=("shopping", "add_to_list", "price_list", "send_to_basket", "orders", "tick_off_order", "clear_list"),
           provides={"assistant.tools": "app.modules.shopping.tools:TOOLS"}),
    Module("bankholidays", "Bank holidays", "GOV.UK's bank holidays: weekday-only bus trackers rest on them",
           category="data", core=True, listed=False, link="https://www.gov.uk/bank-holidays",
           routers=("app.bankholidays:router",), prefixes=("/v1/bank-holidays",), tools=("bank_holidays",)),
    Module("here", "Here", "Everything World Assistant knows about where you are now, without saving a place",
           category="place", keys=("what3words_api_key",), key_optional=True,
           link="https://accounts.what3words.com/select-plan",
           note="Works without a key; with what3words' (free plan) it shows the three words for a place and takes "
                "three words instead of a postcode.",
           routers=("app.modules.here.here:router", "app.modules.here.here_page:page_router"), prefixes=("/v1/here", "/here"), tools=("here",),
           provides={"assistant.tools": "app.modules.here.tools:TOOLS"},
           uses=("codepoint",)),
    Module("crime", "Crime and police", "Street-level crime within a mile of a place and its neighbourhood police team "
           "(data.police.uk), looked up when asked", category="place", link="https://data.police.uk/",
           note="England, Wales and Northern Ireland; the figures are about two months behind.",
           routers=("app.modules.crime.crime:router",), prefixes=("/v1/crime",), tools=("crime",),
           provides={"assistant.tools": "app.modules.crime.tools:TOOLS"},
           uses=("codepoint",)),
    Module("food", "Food standards", "Food hygiene ratings near a place, and the FSA's allergy alerts and product recalls "
           "checked against your shopping list and usual products", category="home", jobs=("food_alerts_check",),
           link="https://ratings.food.gov.uk/",
           note="No key. Alerts are matched against the KitchenOwl list and usual products when shopping is set up.",
           routers=("app.modules.food.food:router",), prefixes=("/v1/food",),
           worker=(Job("app.modules.food.food:food_alerts_check", {"hour": {7}, "minute": {15}, "timeout": 300, "max_tries": 1}),),
           tools=("food_hygiene", "food_alerts"),
           provides={"assistant.tools": "app.modules.food.tools:TOOLS"},
           uses=("crime", "shopping", "wishlist")),
    Module("wishlist", "Wishlist", "A KitchenOwl list of things to look out for in Aldi's Specialbuys and the Middle of Lidl",
           category="home", keys=("kitchenowl_token",), needs=("shopping",), jobs=("wishlist_check",),
           link="https://www.aldi.co.uk/specialbuys",
           note='Add a KitchenOwl list called "Wishlist". Reads Aldi\'s and Lidl\'s own website searches: unofficial, '
                "so it may need fixing when they change",
           routers=("app.modules.wishlist.wishlist:router", "app.modules.wishlist.wishlist_page:page_router"), prefixes=("/v1/wishlist", "/wishlist"),
           worker=(Job("app.modules.wishlist.wishlist:wishlist_check", {"weekday": {2, 5}, "hour": {18}, "minute": {40}, "timeout": 600,
                                                       "max_tries": 1}),),
           tools=("wishlist", "wish_add", "wish_remove", "wishlist_check"),
           provides={"assistant.tools": "app.modules.wishlist.tools:TOOLS"}),
    Module("bikes", "Bike garage", "Every bike's parts and how worn they are, from your rides: what is due, tyre pressures, "
           "batteries, spares and running cost",
           category="home", jobs=("bikes_check", "garmin_sync"),
           note="Add yourself as a rider on the Bikes page, then your bikes. Rides come from Garmin Connect: save "
                "your Garmin Personal API key on the Bikes page (or import its activities CSV). Due parts go onto the "
                "Outdoors list when that is set up",
           routers=("app.modules.bikes.api:router", "app.modules.bikes.garmin:router", "app.modules.bikes.page:page_router"),
           prefixes=("/v1/bikes", "/bikes"),
           worker=(Job("app.modules.bikes.garmin:garmin_sync", {"hour": {6}, "minute": {50}, "timeout": 900, "max_tries": 1}),
                   Job("app.modules.bikes.api:bikes_check", {"hour": {7}, "minute": {40}, "timeout": 300, "max_tries": 1})),
           tools=("bikes", "bike_due", "bike_service", "bike_ride", "tyre_pressure"),
           provides={"assistant.tools": "app.modules.bikes.tools:TOOLS"},
           uses=("environment", "outdoors", "shopping", "wishlist")),
    Module("outdoors", "Outdoors", "A KitchenOwl list of outdoor gear, priced every day at GO Outdoors and Decathlon, with price drops",
           category="home", keys=("kitchenowl_token",), needs=("shopping",), jobs=("outdoors_check",),
           link="https://www.gooutdoors.co.uk",
           note='Add a KitchenOwl list called "Outdoors". GO Outdoors is searched by name; Decathlon only by a pasted link',
           routers=("app.modules.outdoors.outdoors:router", "app.modules.outdoors.outdoors_page:page_router"), prefixes=("/v1/outdoors", "/outdoors"),
           worker=(Job("app.modules.outdoors.outdoors:outdoors_check", {"hour": {7}, "minute": {25}, "timeout": 900, "max_tries": 1}),),
           tools=("outdoors", "outdoors_add", "outdoors_check"),
           provides={"assistant.tools": "app.modules.outdoors.tools:TOOLS"},
           uses=("wishlist",)),
    Module("graph", "Knowledge graph (Neo4j)", "The Graph page: how your places, trips, roads and stations connect",
           category="service", listed=False, switch="graph_enabled", services=("docker-compose.graph.yml",),
           routers=("app.modules.graph.graph_routes:router", "app.modules.graph.graph_routes:page_router"), prefixes=("/v1/graph", "/graph"),
           worker=(Job("app.modules.graph.build:graph_build", {"minute": {8, 38}, "timeout": 300, "max_tries": 1}),), tools=("connections",),
           provides={"assistant.tools": "app.modules.graph.tools:TOOLS", "shutdown": "app.modules.graph.store:close", "setup.check": "app.modules.graph.checks:graph"},
           uses=("events", "gridserve")),
    Module("routing", "Routing (Valhalla)", "Real road routes for trips, so road events, fuel and conditions follow them",
           category="service", listed=False, services=("docker-compose.routing.yml",)),
)

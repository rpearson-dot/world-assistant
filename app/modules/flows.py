"""What each built-in module does with data: its role, what it reads, what it gives, and what it changes in the world.

- **role:** "source" (fetches from outside), "processor" (works from what other modules and Home Assistant hold),
  or "output" (gets things to you: pages, Home Assistant, notifications). Most do a little of each; the role is the
  main one.
- **reads:** outside services, devices on the home network, and Home Assistant (the modules it uses are added from
  `needs` and `uses`, so they are not repeated here).
- **gives:** Home Assistant entities, notifications and anything else that leaves World Assistant (its pages and
  assistant tools are added from `tabs`, `prefixes` and `tools`).
- **controls:** what it changes in the real world. Most modules change nothing; the few that do say exactly what.

Kept here, beside the declarations in builtin.py, so the list reads as one table; a module from outside sets the same
fields on its own Module. tests/test_module_flows.py checks them against the code (a module that publishes to Home
Assistant, sends notifications, reads Home Assistant or commands a device must say so).
"""

from __future__ import annotations

from typing import Any

HA = "Home Assistant"

FLOWS: dict[str, dict[str, Any]] = {
    "nh": {"role": "source", "reads": ("National Highways road events and signs (API)",), "gives": (f"{HA}: road events",)},
    "fuel": {"role": "source", "reads": ("Fuel Finder (GOV.UK): every station's prices",), "gives": (f"{HA}: fuel prices",)},
    "tfl": {"role": "source", "reads": ("TfL Unified API: arrivals, boards, road works",), "gives": (f"{HA}: train boards",)},
    "nr": {"role": "source", "reads": ("National Rail Darwin (Rail Data Marketplace): departures",),
           "gives": ("departures for the Trains page and Here",)},
    "bods": {"role": "source", "reads": ("Bus Open Data Service: live bus positions",)},
    "tfwm": {"role": "source", "reads": ("Transport for West Midlands: predicted bus and tram times",),
             "gives": ("predicted times for the bus tracker",)},
    "events": {"role": "source", "reads": ("Skiddle", "Ticketmaster", "Data Thistle"), "gives": (f"{HA}: events near home",)},
    "pollen": {"role": "source", "reads": ("Polleninformation: pollen by allergen",), "gives": (f"{HA}: pollen",)},
    "octopus": {"role": "source", "reads": ("Octopus Energy: rates, Saving Sessions, meter readings",),
                "gives": (f"{HA}: rates, cheap windows, sessions",)},
    "floods": {"role": "source", "reads": ("Environment Agency flood warnings",), "gives": (f"{HA}: flood warnings",)},
    "environment": {"role": "source", "reads": ("Defra air quality", "Environment Agency river, rain and tide gauges", "NRFA river flows",
                                                "storm overflows (Thames Water)"),
                    "gives": (f"{HA}: air, rivers and tides", "notifications: environment warnings")},
    "tflroads": {"role": "source", "reads": ("TfL road disruptions",), "gives": (f"{HA}: London road disruptions",)},
    "tflstations": {"role": "source", "reads": ("TfL station layouts, facilities and lift status",)},
    "ukpn": {"role": "source", "reads": ("UK Power Networks open data: power cuts and street works",), "gives": (f"{HA}: power cuts and works",)},
    "gridserve": {"role": "source", "reads": ("GRIDSERVE chargers and their status",), "gives": (f"{HA}: EV chargers",)},
    "busdisruptions": {"role": "source", "reads": ("Bus Open Data Service disruptions",)},
    "naptan": {"role": "source", "reads": ("NaPTAN: every stop and station (a download)",)},
    "boundary": {"role": "source", "reads": ("OS Boundary-Line (a download)",),
                 "gives": ("which council and region places, stops and road works are in, for other modules",)},
    "usrn": {"role": "source", "reads": ("OS Open USRN (a download)",), "gives": ("street lines for road works",)},
    "codepoint": {"role": "source", "reads": ("OS Code-Point Open (a download)",)},
    "bus": {"role": "processor", "reads": (), "gives": (f"{HA}: bus tracker",)},
    "buslog": {"role": "source", "reads": ("TfL arrivals", "Bus Open Data Service positions")},
    "ocpp": {"role": "source", "reads": ("the car charger (OCPP 1.6J, on the home network)",),
             "gives": (f"{HA}: charger sensors and switch", "notifications: charger restarts, faults and drops"),
             "controls": ("the car charger: start and stop charging, Off/On/Auto, availability",)},
    "ovms": {"role": "source", "reads": ("the car's OVMS module (through the MQTT broker)",),
             "gives": ("notifications: car warnings, car gone quiet",)},
    "airstage": {"role": "source", "reads": ("the air conditioners (Fujitsu Airstage, on the home network)",),
                 "gives": (f"{HA}: a climate entity for each unit", "notifications: error codes, filters, units not answering"),
                 "controls": ("the air conditioners: on and off, mode, target, fan, swing and extras (from the page, the assistant or Home Assistant)",)},
    "weather": {"role": "source", "reads": ("Open-Meteo forecast and archive",), "gives": (f"{HA}: temperature and heating degree-hours",)},
    "solar": {"role": "source", "reads": ("forecast.solar", f"{HA}: the panels' output statistic (accuracy check)",
                                          f"{HA}: Forecast.Solar arrays (copying, through the share)"),
              "gives": (f"{HA}: solar expected today, still to come, tomorrow, power now",)},
    "planner": {"role": "processor", "reads": (f"{HA}: the house's use statistic, the plan, the charger and car sensors, the battery and grid entities",),
                "gives": ("notifications: the planner's month",),
                "controls": ("the car charger: start and stop, only with \"act\" ticked and the switch on Auto",)},
    "tariffs": {"role": "processor", "reads": ("Octopus Energy's public tariffs",)},
    "conditions": {"role": "processor", "reads": (), "gives": (f"{HA}: trip conditions",)},
    "vehicles": {"role": "processor", "reads": ()},
    "vehiclechecks": {"role": "source", "reads": ("DVLA Vehicle Enquiry Service", "DVSA MOT history"), "gives": (f"{HA}: tax and MOT",)},
    "shopping": {"role": "processor", "reads": ("KitchenOwl (your list)", "Sainsbury's and Ocado prices (the supermarket service)")},
    "here": {"role": "output", "reads": ()},
    "crime": {"role": "source", "reads": ("data.police.uk", "Nomis (populations)")},
    "food": {"role": "source", "reads": ("Food Standards Agency: hygiene ratings, allergy alerts and recalls",), "gives": (f"{HA}: food alerts",)},
    "wishlist": {"role": "source", "reads": ("Aldi and Lidl special offers",), "gives": (f"{HA}: wishlist matches",)},
    "bikes": {"role": "processor", "reads": ("Garmin Connect (your rides, Personal API)", "GOV.UK product recalls", "BikeRegister"),
              "gives": ("notifications: bike batteries, parts due, firmware",)},
    "outdoors": {"role": "source", "reads": ("GO Outdoors, Decathlon and other shops' prices",), "gives": (f"{HA}: outdoor gear prices",)},
    "graph": {"role": "output", "reads": ("Neo4j (the optional graph container)",)},
    "routing": {"role": "processor", "reads": ("Valhalla (the optional routing container, OpenStreetMap)",),
                "gives": ("road routes for trips, so conditions and fuel follow them",)},
}


def flow(m: Any) -> dict[str, Any]:
    """A module's role, inputs, outputs and controls in full: what it declares, plus what its declaration already
    says (the modules it uses, its pages, its assistant tools)."""
    inputs = list(m.reads) + [f"module: {x}" for x in (*m.needs, *m.uses)]
    pages = [label for _sec, _pid, _path, label in m.tabs] or [p for p in m.prefixes if not p.startswith("/v1")]
    outputs = [f"page: {p}" for p in pages] + list(m.gives)
    if m.tools:
        outputs.append("assistant: " + ", ".join(m.tools))
    if any(p.startswith("/v1") for p in m.prefixes):
        outputs.append("API: " + ", ".join(p for p in m.prefixes if p.startswith("/v1")))
    return {"role": m.role, "inputs": inputs, "outputs": outputs, "controls": list(m.controls)}

from __future__ import annotations

from app.integrations.nationalhighways.match import (
    Corridor,
    distance_to_line_m,
    match_events,
    match_signs,
)
from app.integrations.nationalhighways.normalise import (
    clean_sign_line,
    iter_events,
    iter_vms,
    kmh_to_mph,
    parse_pos_list,
    parse_time,
)

LINE = "51.30 -0.50 51.31 -0.50 51.32 -0.50"


def _speed_payload(road: str = "M25", status: str = "active", kmh: float = 64.3738) -> dict:
    return {
        "situation": [
            {
                "idG": "signs/M25-S1A",
                "situationRecord": [
                    {
                        "sitSpeedManagement": {
                            "idG": "rec-1",
                            "validity": {
                                "validityStatus": status,
                                "validityTimeSpecification": {
                                    "overallStartTime": "2026-10-20T15:00:00.00Z",
                                    "overallEndTime": "2026-10-20T15:15:00.00Z",
                                },
                            },
                            "complianceOption": "mandatory",
                            "temporarySpeedLimit": kmh,
                            "locationReference": {
                                "locLinearLocation": {
                                    "gmlLineString": {"locGmlLineString": {"posList": LINE}},
                                    "supplementaryPositionalDescription": {
                                        "locationDescription": f"{road} clockwise within J11"
                                    },
                                },
                                "locSingleRoadLinearLocation": {
                                    "linearWithinLinearElement": [
                                        {
                                            "linearElement": {
                                                "locLinearElementByCode": {"roadName": road}
                                            },
                                            "directionOnLinearSection": "clockwise",
                                        }
                                    ]
                                },
                            },
                        }
                    }
                ],
            }
        ]
    }


def _grouped_closure_payload() -> dict:
    part = {
        "locLinearLocation": {"gmlLineString": {"locGmlLineString": {"posList": LINE}}},
        "locSingleRoadLinearLocation": {
            "linearWithinLinearElement": [
                {
                    "linearElement": {"locLinearElementByCode": {"roadName": "M25"}},
                    "directionOnLinearSection": "clockwise",
                }
            ]
        },
    }
    return {
        "situation": [
            {
                "idG": "s",
                "situationRecord": [
                    {
                        "sitRoadOrCarriagewayOrLaneManagement": {
                            "idG": "c-1",
                            "validity": {"validityStatus": "planned"},
                            "roadOrCarriagewayOrLaneManagementType": {"value": "laneClosures"},
                            "locationReference": {
                                "locLocationGroupByList": {
                                    "locationContainedInGroup": [part, part]
                                }
                            },
                        }
                    }
                ],
            }
        ]
    }


def test_parse_helpers() -> None:
    assert parse_pos_list("1 2 3 4") == ((1.0, 2.0), (3.0, 4.0))
    assert parse_pos_list("") == ()
    assert parse_pos_list("a b") == ()
    assert parse_time("2026-10-20T15:31:36.36Z").hour == 15
    assert parse_time(None) is None
    assert kmh_to_mph(64.3738) == 40
    assert kmh_to_mph(0.0) is None
    assert clean_sign_line("Queue | ahead[hca1]") == "Queue | ahead"


def test_speed_event_parsed() -> None:
    (event,) = list(iter_events(_speed_payload()))
    assert event.kind == "speed"
    assert event.limit_mph == 40
    assert event.detail == "mandatory"
    assert event.status == "active"
    assert event.roads == {"M25"}
    assert event.segments[0].direction == "clockwise"
    assert event.segments[0].description == "M25 clockwise within J11"
    assert len(event.segments[0].points) == 3


def test_grouped_closure_has_two_segments() -> None:
    (event,) = list(iter_events(_grouped_closure_payload()))
    assert event.kind == "closure"
    assert event.detail == "laneClosures"
    assert len(event.segments) == 2


def test_distance_to_line() -> None:
    line = ((51.30, -0.50), (51.32, -0.50))
    assert distance_to_line_m((51.31, -0.50), line) < 1
    # 0.001 degrees of longitude at this latitude is about 69 m
    assert 60 < distance_to_line_m((51.31, -0.499), line) < 80


def test_matching_by_distance_and_road() -> None:
    events = list(iter_events(_speed_payload()))
    near = Corridor("a", "near", ((51.30, -0.499), (51.32, -0.499)), buffer_m=500)
    far = Corridor("b", "far", ((52.30, -1.5), (52.32, -1.5)), buffer_m=500)
    other_road = Corridor("c", "m40", near.centreline, buffer_m=500, roads=frozenset({"M40"}))
    same_road = Corridor("d", "m25", near.centreline, buffer_m=500, roads=frozenset({"M25"}))
    assert match_events(events, near) == events
    assert match_events(events, far) == []
    assert match_events(events, other_road) == []
    assert match_events(events, same_road) == events


def test_vms_parsed_and_matched() -> None:
    payload = {
        "vmsControllerStatus": [
            {
                "vmsControllerReference": {"idG": "signs/M25-M1A"},
                "vmsStatus": [
                    {
                        "vmsStatus": {
                            "workingStatus": "working ",
                            "vmsMessage": [
                                {
                                    "vmsMessage": {
                                        "timeLastSet": "2026-10-20T15:31:11.11Z",
                                        "displayAreaSettings": [
                                            {
                                                "displayAreaSettings": {
                                                    "vmsTextDisplay": {
                                                        "textLine": [
                                                            {"textLine": {"textLine": "QUEUE"}},
                                                            {"textLine": {"textLine": "[hca1]"}},
                                                        ]
                                                    }
                                                }
                                            }
                                        ],
                                    }
                                }
                            ],
                            "vmsStatusExtensionG": {
                                "vmsLocation": {
                                    "locPointLocation": {
                                        "pointByCoordinates": {
                                            "pointCoordinates": {"latitude": 51.31, "longitude": -0.5}
                                        },
                                        "supplementaryPositionalDescription": {
                                            "roadInformation": [{"roadName": "M25"}],
                                            "locationDescription": "M25 clockwise within J11",
                                            "supplementaryPositionalDescriptionExtensionG": {
                                                "direction": "clockwise"
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    }
                ],
            }
        ]
    }
    (sign,) = list(iter_vms(payload))
    assert sign.working
    assert sign.lines == ("QUEUE",)
    assert sign.text == "QUEUE"
    assert sign.road == "M25"
    corridor = Corridor("a", "a", ((51.30, -0.5), (51.32, -0.5)), buffer_m=200)
    assert match_signs([sign], corridor) == [sign]
    assert match_signs([sign], Corridor("b", "b", ((52.0, -1.0), (52.1, -1.0)))) == []


def test_corridor_bbox_contains_centreline() -> None:
    c = Corridor("a", "a", ((51.30, -0.5), (51.32, -0.4)), buffer_m=1000)
    lat0, lon0, lat1, lon1 = c.bbox()
    assert lat0 < 51.30
    assert lat1 > 51.32
    assert lon0 < -0.5
    assert lon1 > -0.4

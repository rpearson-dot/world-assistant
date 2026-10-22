import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.energy import octopus, store
from app.energy import rates as R
from app.energy.ha import energy_device
from app.energy.summary import slots, summarise

D = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)


def P(h0, h1, pence):
    return R.Period(D + timedelta(hours=h0), D + timedelta(hours=h1), pence)


def cosy():
    # cheap 04-07 and 13-16 and 22-24, peak 16-19, standard otherwise
    return [
        P(0, 4, 26),
        P(4, 7, 13),
        P(7, 13, 26),
        P(13, 16, 13),
        P(16, 19, 39),
        P(19, 22, 26),
        P(22, 24, 13),
    ]


def test_product_of():
    assert octopus.product_of("E-1R-COSY-22-12-08-A") == "COSY-22-12-08"
    assert octopus.product_of("e-2r-agile-24-10-01-c") == "AGILE-24-10-01"


def test_bad_tariff():
    try:
        octopus.product_of("G-1R-X-A")
    except octopus.OctopusError:
        return
    raise AssertionError


def test_three_bands():
    banded = {p.pence: b for p, b in R.band_of(cosy())}
    assert banded == {26: R.STANDARD, 13: R.CHEAP, 39: R.PEAK}


def test_single_and_two_price():
    assert {b for _, b in R.band_of([P(0, 24, 25)])} == {R.STANDARD}
    assert [b for _, b in R.band_of([P(0, 12, 10), P(12, 24, 20)])] == [R.CHEAP, R.STANDARD]


def test_many_prices_negative_is_cheap():
    ps = [P(i, i + 1, v) for i, v in enumerate([-2, 5, 10, 15, 20, 25, 30, 35])]
    bands = [b for _, b in R.band_of(ps)]
    assert bands[0] == R.CHEAP and bands[-1] == R.PEAK


def test_windows_merge_adjacent_same_band():
    wins = R.windows([P(0, 1, 13), P(1, 2, 13), P(2, 3, 26), P(3, 4, 13)])
    assert [(w.band, w.hours) for w in wins] == [(R.CHEAP, 2.0), (R.STANDARD, 1.0), (R.CHEAP, 1.0)]


def test_gap_splits_window():
    wins = R.windows([P(0, 1, 13), P(2, 3, 13), P(3, 4, 26)])
    assert len(wins) == 3


def test_summary_midday():
    now = D + timedelta(hours=14)
    s = summarise("T", cosy(), now)
    assert s["now"]["band"] == "cheap" and s["cheap_now"] is True
    assert s["next"]["band"] == "peak" and s["next"]["minutes"] == 120
    assert s["next_peak"]["start"] == (D + timedelta(hours=16)).isoformat()
    assert s["next_cheap"]["end"] == (D + timedelta(hours=16)).isoformat()  # the one on now


def test_summary_overnight_next_cheap():
    now = D + timedelta(hours=1)
    s = summarise("T", cosy(), now)
    assert s["now"]["band"] == "standard"
    assert s["next_cheap"]["start"] == (D + timedelta(hours=4)).isoformat()


def test_cheapest_block():
    b = R.cheapest_block(cosy(), 3, D + timedelta(hours=1))
    assert b is not None and b.start == D + timedelta(hours=4) and b.hours == 3 and b.pence == 13


def test_cheapest_block_needs_coverage():
    assert R.cheapest_block([P(0, 1, 5)], 3, D) is None


def test_no_data():
    assert summarise(None, [], D)["available"] is False
    assert summarise("T", cosy(), D + timedelta(days=3))["available"] is False


def test_normalise_overlap_prefers_later():
    out = R.normalise([P(0, 4, 10), P(2, 3, 20)])
    assert out[0].end == D + timedelta(hours=2) and out[-1].pence == 20


def test_pick_tariff_current_import():
    acct = {
        "properties": [
            {
                "electricity_meter_points": [
                    {
                        "is_export": False,
                        "agreements": [
                            {
                                "tariff_code": "E-1R-VAR-22-11-01-A",
                                "valid_from": "2022-01-01T00:00:00Z",
                                "valid_to": "2024-01-01T00:00:00Z",
                            },
                            {
                                "tariff_code": "E-1R-COSY-22-12-08-A",
                                "valid_from": "2024-01-01T00:00:00Z",
                                "valid_to": None,
                            },
                        ],
                    },
                    {
                        "is_export": True,
                        "agreements": [
                            {
                                "tariff_code": "E-1R-OUTGOING-FIX-12M-19-05-13-A",
                                "valid_from": "2024-01-01T00:00:00Z",
                                "valid_to": None,
                            }
                        ],
                    },
                ]
            }
        ]
    }
    assert octopus.pick_tariff(acct, D) == "E-1R-COSY-22-12-08-A"


def test_pick_tariff_none():
    try:
        octopus.pick_tariff({"properties": []}, D)
    except octopus.OctopusError:
        return
    raise AssertionError


def test_parse_rates():
    body = {
        "results": [
            {
                "value_exc_vat": 12.4,
                "value_inc_vat": 13.02,
                "valid_from": "2026-10-23T04:00:00Z",
                "valid_to": "2026-10-23T07:00:00Z",
            },
            {"value_inc_vat": None, "valid_from": "2026-10-23T07:00:00Z"},
        ]
    }
    rows = octopus.parse_rates(body)
    assert len(rows) == 1 and rows[0]["pence_inc_vat"] == 13.02


def test_device():
    now = D + timedelta(hours=14)
    s = summarise("T", cosy(), now)
    dev = energy_device(s, slots(cosy(), now))
    keys = {x.key: x for x in dev.sensors}
    assert keys["cheap_now"].state == "on" and keys["rate"].state == 13
    assert keys["slots"].attributes["slots"]


def test_device_unavailable():
    assert energy_device({"available": False}, []).sensors[0].key == "status"


# --- database: migration 0023, save and load --------------------------------------------------------------


URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"


@pytest.fixture
def schema():
    assert URL
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS energy_rate"))
        with Operations.context(MigrationContext.configure(conn)):
            spec = importlib.util.spec_from_file_location("m0023", VERSIONS / "0023_energy_rate.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS energy_rate"))
    engine.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_save_and_load(schema) -> None:
    engine = create_async_engine(URL)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    rows = [
        {
            "valid_from": now,
            "valid_to": now + timedelta(hours=3),
            "pence_inc_vat": 13.0,
            "pence_exc_vat": 12.4,
        },
        {
            "valid_from": now + timedelta(hours=3),
            "valid_to": None,
            "pence_inc_vat": 26.0,
            "pence_exc_vat": None,
        },
    ]
    try:
        assert await store.save(engine, "E-1R-COSY-22-12-08-A", rows) == 2
        assert await store.save(engine, "E-1R-COSY-22-12-08-A", rows) == 2  # idempotent
        tariff, periods = await store.load(engine, now)
        assert tariff == "E-1R-COSY-22-12-08-A" and [p.pence for p in periods] == [13.0, 26.0]
        assert periods[0].end == periods[1].start
        await store.save(engine, "OTHER", rows[:1])  # a changed tariff replaces the old rows
        assert (await store.load(engine, now))[0] == "OTHER"
    finally:
        await engine.dispose()

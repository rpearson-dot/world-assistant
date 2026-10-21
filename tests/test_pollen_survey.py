from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

spec = importlib.util.spec_from_file_location("pollen_survey", Path(__file__).resolve().parent.parent / "scripts" / "pollen-survey.py")
assert spec and spec.loader
survey = importlib.util.module_from_spec(spec)
spec.loader.exec_module(survey)


def test_same_forecast_same_fingerprint_and_unrelated_fields_ignored() -> None:
    a = {"contamination": [{"poll_id": 1, "contamination_1": 2}], "allergyrisk": {"allergyrisk_1": 3}, "x": 1}
    b = {**a, "x": 2}
    c = {**a, "allergyrisk": {"allergyrisk_1": 4}}
    assert survey.fingerprint(a) == survey.fingerprint(b) != survey.fingerprint(c)
    assert survey.fingerprint([]) == "error"


def test_runs_group_consecutive_equal_answers() -> None:
    pts = [((0.0, 0.0), "a"), ((0.0, 1.0), "a"), ((0.0, 2.0), "b"), ((0.0, 3.0), "a")]
    assert survey.runs(pts) == [((0.0, 0.0), (0.0, 1.0), "a", 2), ((0.0, 2.0), (0.0, 2.0), "b", 1), ((0.0, 3.0), (0.0, 3.0), "a", 1)]


@pytest.mark.asyncio
async def test_survey_counts_distinct_answers_with_a_fake_provider() -> None:
    async def raw_for(lat: float, lon: float) -> Any:
        cell = (round(lat / 2), round(lon / 2))  # a provider that answers per 2 degree cell
        return {"contamination": [], "allergyrisk": {"a": list(cell)}, "region": "UK"}

    r = await survey.survey(raw_for, pause=0)
    assert r["extra_fields"] == {"region": "UK"}
    assert r["grid_points"] == 72 and 1 < r["grid_distinct"] < 72
    assert sum(len(g) for g in r["grid_groups"]) == 72
    assert set(r["transects"]) == set(survey.transect_points())

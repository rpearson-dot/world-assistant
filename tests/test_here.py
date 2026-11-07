"""Here: every source asked about a point at once, each part in words, and a failing or switched-off part on its own."""

from __future__ import annotations

import re
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request

from app import here as H

ANSWERS: dict[str, Any] = {
    "/v1/postcodes/near": {"postcodes": [{"postcode": "KT17 1AA", "district": "Epsom and Ewell", "ward": "Town"}]},
    "/v1/stops/near": {"stops": [{"name": "Epsom Station", "indicator": "Stop C", "km": 0.12}]},
    "/v1/live-buses": {"buses": [{"line": "406"}, {"line": "293"}, {"line": "406"}]},
    "/v1/stations/near": {"stations": []},
    "/v1/floods/at": {"warnings": []},
    "/v1/environment/air": {"index": 3, "band": "Low", "worst": "PM2.5"},
    "/v1/environment/rivers": {"rivers": [{"river": "Hogsmill", "state": "normal", "trend": "steady"}]},
    "/v1/environment/rain": {"gauges": [{"gauge": "Epsom", "km": 2.0, "last_hour_mm": 0.2, "last_24h_mm": 4.1}]},
    "/v1/environment/tides": {"gauge": None},
    "/v1/environment/overflows": {"discharging": [{}], "recent": [], "count": 7},
    "/v1/pollen/near": {"allergy_risk_label": ["Low"], "allergens": [{"name": "Grass", "levels": [0], "labels": ["None"]}]},
    "/v1/fuel/near": {"stations": [{"brand": "Tesco", "pence": 135.9, "distance_km": 1.6}]},
    "/v1/ev/sites": {"sites": [{"summary": "MOTO Cobham: 4 of 6 free."}]},
    "/v1/events/near": {"events": [{"name": "Christmas market", "venue": "Market Place", "date": "Sat 7 Nov"}], "total": 1},
    "/v1/roads/tfl/at": {"disruptions": []},
}


def fake_app(seen: list[tuple[str, dict[str, str]]]) -> FastAPI:
    app = FastAPI()

    @app.get("/{path:path}")
    async def anything(request: Request, path: str) -> Any:
        p = "/" + path
        seen.append((p, dict(request.query_params)))
        if p == "/v1/power/cuts":
            raise HTTPException(404, "Power cuts is switched off. Turn it on at /modules.")
        if p == "/v1/environment/tides":
            raise HTTPException(502, "Environment Agency answered HTTP 503")
        return ANSWERS[p]

    return app


async def test_every_part_for_a_point(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    req: Any = SimpleNamespace(app=fake_app(seen))
    d = await H.gather(req, 51.3343, -0.2678)
    by = {p["part"]: p for p in d["parts"]}
    assert d["area"]["postcode"] == "KT17 1AA" and len(d["parts"]) == len(H.PARTS)
    assert by["stops"]["text"] == "Nearest: Epsom Station (Stop C), 120 m."
    assert by["buses"]["text"] == "3 buses reporting within a mile, on 293, 406."
    assert by["air"]["text"] == "Index 3 (low), worst PM2.5."
    assert by["rivers"]["text"] == "Hogsmill: normal, steady."
    assert by["rain"]["text"].startswith("0.2 mm in the last hour, 4.1 mm in 24 hours (Epsom")
    assert by["overflows"]["text"] == "1 discharging now (of 7 within 3 km)."
    assert by["fuel"]["text"] == "Cheapest: Tesco, 135.9p, 1.0 mile."
    assert by["chargers"]["text"] == "MOTO Cobham: 4 of 6 free."
    assert by["events"]["text"] == "Christmas market at Market Place, Sat 7 Nov."
    assert by["stations"]["blank"] and by["roads"]["blank"]  # London-only, away from London: left off the page
    assert by["power"]["off"] and not by["power"]["ok"]  # switched off says so, and the rest still came
    assert not by["tides"]["ok"] and "503" in by["tides"]["text"]  # a failing source, in words
    q = dict(seen)
    assert q["/v1/power/cuts"]["postcode"] == "KT17 1AA" and q["/v1/fuel/near"]["lat"] == "51.3343"
    assert q["/v1/environment/air"]["lon"] == "-0.2678"


async def test_the_tool_takes_a_point_or_a_postcode(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import assistant_more as M
    from app import postcodes
    from app.geo import Located

    async def find(engine: Any, value: str) -> Located | None:
        return Located("KT17 1AA", 51.3343, -0.2678) if value else None

    monkeypatch.setattr(postcodes, "local_postcode", find)
    seen: list[tuple[str, dict[str, str]]] = []
    req: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=None)))
    req.app = fake_app(seen)
    req.app.state.engine = None
    r = await M.here_tool(req, {"postcode": "KT17 1AA", "about": "air,fuel"})
    assert r["text"] == "Around KT17 1AA, Epsom and Ewell. Air quality: Index 3 (low), worst PM2.5. Fuel (E10): Cheapest: Tesco, 135.9p, 1.0 mile."
    from app.assistant_base import ToolError

    with pytest.raises(ToolError, match="outside Great Britain"):
        await M.here_tool(req, {"lat": 48.8, "lon": 2.3})
    with pytest.raises(ToolError, match="Give lat and lon"):
        await M.here_tool(req, {})


def test_the_page_script_compiles_and_calls_real_routes(tmp_path: Any) -> None:
    from app.here_page import PAGE
    from app.main import app

    js = re.findall(r"<script>(.*?)</script>", PAGE, re.DOTALL)[-1]
    f = tmp_path / "here.js"
    f.write_text(js)
    subprocess.run(["node", "--check", str(f)], check=True)
    paths = set(app.openapi()["paths"])
    for p in set(re.findall(r"'(/v1/here[^'?]*)", PAGE)):
        assert p.rstrip("/") in paths, p

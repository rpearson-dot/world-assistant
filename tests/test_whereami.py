"""Every page with a place list opens on Where I am, then home, then the rest A to Z."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

PAGES = ["/pollen", "/events", "/fuel", "/map", "/environment", "/environment/floods", "/environment/tides"]


@pytest.mark.parametrize("path", PAGES)
def test_place_pages_share_where_i_am(path: str) -> None:
    html = TestClient(app).get(path).text
    assert "/*WHERE*/" not in html and "function waOrder" in html and "Where I am" in html
    assert "const homeId" not in html  # the old home-first default, alphabetical otherwise, is gone

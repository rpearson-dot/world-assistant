import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.parametrize("path", ["/status", "/bus", "/trains", "/fuel", "/places", "/events", "/pollen", "/energy"])
def test_every_page_is_themed_and_has_nav(path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert "/*BASE_CSS*/" not in r.text and "<!--NAV-->" not in r.text
    assert "--accent" in r.text and '<nav>' in r.text
    assert r.text.count('class="here"') == 1
    assert "http://" not in r.text and "https://" not in r.text  # no external resources

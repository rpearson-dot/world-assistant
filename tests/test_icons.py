from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.icons import NAMES
from app.main import app

ROOT = Path(__file__).resolve().parent.parent


def test_icons_are_served_without_a_token_and_only_the_known_ones() -> None:
    c = TestClient(app)
    for name in NAMES:
        r = c.get(f"/icons/{name}.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.content.startswith(b"\x89PNG")
    assert c.get("/icons/secret.png").status_code == 404
    assert c.get("/icons/..%2Fconfig.png").status_code == 404


def test_every_compose_icon_label_names_a_real_icon() -> None:
    seen = set()
    for f in ("docker-compose.yml", "docker-compose.routing.yml"):
        doc = yaml.safe_load((ROOT / f).read_text())
        for svc in doc["services"].values():
            icon = (svc.get("labels") or {}).get("net.unraid.docker.icon")
            if icon:
                name = icon.rsplit("/", 1)[1].removesuffix(".png")
                assert name in NAMES, icon
                seen.add(name)
    assert seen == NAMES  # each icon is used

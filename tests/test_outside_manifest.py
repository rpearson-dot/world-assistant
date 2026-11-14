"""Outside modules: the manifest (wa-module.toml) read and checked, what an update asks for that the installed
version did not, and when a release installs by itself or is held for you."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.outside import manifest as M
from app.outside import policy as P

GOOD = """
id = "tigo"
name = "Tigo optimisers"
version = "1.2.0"
sdk = 3
min_wa = "0.55.0"
licence = "MIT"
repository = "https://github.com/someone/wa-tigo"
role = "source"
reads = ["the Tigo CCA on the home network"]
gives = ["Home Assistant: power for each panel"]

[image]
name = "ghcr.io/someone/wa-tigo"

[keys]
tigo_password = { label = "CCA password" }

[network]
home_devices = ["Tigo CCA"]

[data]
home_location = "postcode district"
home_assistant_write = ["sensor.wa_x_tigo_*"]
notifications = true

[limits]
storage_mb = 200

[[settings]]
key = "every"
label = "Read every (seconds)"
type = "number"
default = 30
"""


def parse(text: str = GOOD) -> M.Manifest:
    return M.parse(text, "0.55.0", 3)


def test_a_good_manifest() -> None:
    m = parse()
    assert (m.id, m.role, m.image, m.data, m.notifications) == ("tigo", "source", "ghcr.io/someone/wa-tigo",
                                                                 {"home_location": "postcode district"}, True)  # fmt: skip
    assert m.limits["storage_mb"] == 200 and m.limits["memory_mb"] == 256 and m.keys["tigo_password"]["required"] is False
    assert m.settings[0].type == "number" and M.from_json(m.to_json()) == m


@pytest.mark.parametrize(("change", "words"), [
    (("id = \"tigo\"", "id = \"Tigo!\""), "id:"),
    (("sdk = 3", "sdk = 4"), "update World Assistant"),
    (("min_wa = \"0.55.0\"", "min_wa = \"0.56.0\""), "World Assistant 0.56.0"),
    (("licence = \"MIT\"", "licence = \"Proprietary\""), "open-source"),
    (("name = \"ghcr.io/someone/wa-tigo\"", "name = \"ghcr.io/other/wa-tigo\""), "own owner"),
    (("name = \"ghcr.io/someone/wa-tigo\"", "name = \"docker.io/someone/wa-tigo\""), "ghcr.io"),
    (("sensor.wa_x_tigo_*", "sensor.living_room_*"), "only its own entities"),
    (("storage_mb = 200", "storage_mb = 99999"), "limits.storage_mb"),
    (("home_location = \"postcode district\"", "home_location = \"street\""), "home_location"),
    (("role = \"source\"", "role = \"thing\""), "role"),
    (("id = \"tigo\"", "colour = \"red\"\nid = \"tigo\""), "unknown fields"),
])
def test_a_bad_manifest_says_why(change: tuple[str, str], words: str) -> None:
    with pytest.raises(M.ManifestError, match=words.replace("(", r"\(")):
        parse(GOOD.replace(*change))


def test_personal_data_sent_outside_needs_a_reason() -> None:
    text = GOOD.replace('home_devices = ["Tigo CCA"]', 'hosts = ["api.tigo.example"]')
    with pytest.raises(M.ManifestError, match="say why"):
        parse(text)
    assert parse(text + '\n[purposes]\n"api.tigo.example" = "the panels\' location for the weather"\n').hosts == ("api.tigo.example",)


def test_what_an_update_asks_for_that_was_not_agreed() -> None:
    old = parse()
    assert M.more_access(old, old) == []
    new = parse(GOOD.replace('home_location = "postcode district"', 'home_location = "exact"\nplaces = true')
                .replace('home_devices = ["Tigo CCA"]', 'home_devices = ["Tigo CCA", "Inverter"]')
                .replace("storage_mb = 200", "storage_mb = 300"))
    more = M.more_access(old, new)
    assert "your home's location more precisely (exact, was postcode district)" in more and "your saved places" in more
    assert "a device on your network: Inverter" in more and "a higher limit: storage mb 200 → 300" in more
    assert M.more_access(new, old) == []  # asking for less is never held


def test_an_update_installs_by_itself_unless_it_needs_you() -> None:
    old, ok = parse(), P.Checks(signed=True, provenance=True, sbom=True, licence="MIT")
    new = parse(GOOD.replace('version = "1.2.0"', 'version = "1.3.0"'))
    assert P.update(old, new, ok) == ("install", [])
    assert P.update(old, new, ok, auto=False)[0] == "hold"
    held, why = P.update(old, new, P.Checks(**{**ok.__dict__, "critical": 1}))
    assert held == "hold" and "1 critical" in why[0]
    assert "not signed" in P.update(old, new, P.Checks(provenance=True, sbom=True))[1][0]
    more = parse(GOOD.replace("notifications = true", "notifications = true\nvehicles = true"))
    assert P.update(old, more, ok)[1] == ["it asks for more: your vehicles (registrations)"]
    gpl = parse(GOOD.replace('licence = "MIT"', 'licence = "GPL-3.0-only"'))
    assert any("licence changed" in r for r in P.update(old, gpl, P.Checks(**{**ok.__dict__, "licence": "GPL-3.0-only"}))[1])
    assert "could not be completed" in P.update(old, new, P.Checks(error="no answer from ghcr.io"))[1][0]


def test_falling_behind_is_a_warning() -> None:
    now = datetime(2026, 11, 14, tzinfo=UTC)
    assert P.warnings({}, now) == []
    w = P.warnings({"held_since": now - timedelta(days=9), "installed_checks": {"high": 2},
                    "last_release_at": now - timedelta(days=400)}, now)  # fmt: skip
    assert w[0] == "a newer release has waited for you for 9 days" and "2 high-severity" in w[1] and "13 months" in w[2]

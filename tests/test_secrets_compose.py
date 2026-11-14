"""A new secret file in docker-compose.yml breaks the next update: the updater cannot create files in the secrets
folder (read-only to it), and Docker refuses to start a container whose secret file is missing. That is how 0.52.1's
update failed. New keys are stored the Keys page's way instead (app/secretstore.py: web_value), which needs no file."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# The secret files every install already has (as of 0.52.0). Never add to this list.
EXISTING = {"api_token", "bods_api_key", "datathistle_refresh_token", "datathistle_token", "dvla_api_key", "dvsa_mot_api_key",
            "dvsa_mot_client_id", "dvsa_mot_client_secret", "dvsa_mot_token_url", "fuel_finder_client_id",
            "fuel_finder_client_secret", "gridserve_api_key", "kitchenowl_token", "mqtt_password", "nh_subscription_key",
            "nh_subscription_key_secondary", "nr_access_token", "ocado_email", "ocado_password", "octopus_api_key",
            "pollen_api_key", "postgres_password", "redis_password", "sainsburys_email", "sainsburys_password",
            "skiddle_api_key", "tfl_app_key", "tfwm_app_id", "tfwm_app_key", "ticketmaster_api_key", "ukpn_api_key",
            "what3words_api_key"}  # fmt: skip


def test_no_new_secret_files() -> None:
    for name in ("docker-compose.yml",):
        doc = yaml.safe_load((ROOT / name).read_text())
        new = set(doc.get("secrets") or {}) - EXISTING
        assert not new, f"{name}: new secret files {sorted(new)} would stop the next update; store them through the Keys page instead"

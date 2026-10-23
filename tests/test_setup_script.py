"""scripts/setup.sh, driven with typed answers in a scratch copy of the project (it never starts anything here)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KEYS = 13  # key prompts, in order: nh, nh secondary, fuel id, fuel secret, tfl, nr, bods, tfwm id, tfwm key, skiddle, tm, pollen, octopus


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    for name in ("setup.sh", "init-secrets.sh"):
        shutil.copy(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def run(project: Path, answers: list[str]) -> str:
    r = subprocess.run(["bash", "scripts/setup.sh", "--no-start"], cwd=project, input="\n".join(answers) + "\n",
                       capture_output=True, text=True, timeout=60, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def env(project: Path) -> dict[str, str]:
    out = {}
    for line in (project / ".env").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def test_a_new_install_with_the_bundled_broker(project: Path) -> None:
    keys = [""] * KEYS
    keys[4] = "my-tfl-key"
    text = run(project, ["", "", "", "2", "", "n", "n", "n", "", *keys])
    e = env(project)
    assert e["WA_DATA_DIR"] == "./data" and e["WA_SECRETS_DIR"] == "./secrets" and e["WA_API_PORT"] == "8010"
    assert e["WA_MQTT_HOST"] == "wa-mqtt" and e["WA_MQTT_USER"] == "wa"
    assert e["COMPOSE_FILE"] == "docker-compose.yml:docker-compose.mqtt.yml"
    s = project / "secrets"
    assert (s / "tfl_app_key.txt").read_text() == "my-tfl-key"
    assert len((s / "mqtt_password.txt").read_text()) >= 32  # generated for the bundled broker
    assert (s / "api_token.txt").read_text() and (s / "nh_subscription_key.txt").read_text() == ""
    assert "my-tfl-key" not in text  # keys are never shown
    assert (project / "data" / "backups").is_dir()


def test_running_it_again_keeps_everything(project: Path) -> None:
    keys = [""] * KEYS
    keys[0] = "nh-key"
    run(project, ["", "", "9000", "2", "", "y", "y", "y", "", "ts-key", *keys])
    before, token = env(project), (project / "secrets" / "api_token.txt").read_text()
    run(project, ["", "", "", "", "", "", "", "", "", "", *([""] * KEYS)])
    assert env(project) == before and before["WA_API_PORT"] == "9000"
    assert before["COMPOSE_FILE"] == (
        "docker-compose.yml:docker-compose.mqtt.yml:docker-compose.routing.yml:docker-compose.graph.yml"
        ":docker-compose.tailscale.yml"
    )
    assert before["WA_GRAPH_ENABLED"] == "true"
    assert (project / "secrets" / "tailscale_authkey.txt").read_text() == "ts-key"
    assert (project / "secrets" / "nh_subscription_key.txt").read_text() == "nh-key"
    assert (project / "secrets" / "api_token.txt").read_text() == token


def test_your_own_broker_or_none(project: Path) -> None:
    run(project, ["", "", "", "1", "192.168.1.10", "", "mqttuser", "n", "n", "n", "", "broker-pw", *([""] * KEYS)])
    e = env(project)
    assert (e["WA_MQTT_HOST"], e["WA_MQTT_PORT"], e["WA_MQTT_USER"]) == ("192.168.1.10", "1883", "mqttuser")
    assert e["COMPOSE_FILE"] == "docker-compose.yml"
    assert (project / "secrets" / "mqtt_password.txt").read_text() == "broker-pw"
    run(project, ["", "", "", "3", "n", "n", "n", "", *([""] * KEYS)])
    assert env(project)["WA_MQTT_HOST"] == ""


def test_other_overlays_are_kept(project: Path) -> None:
    shutil.copy(project / ".env.example", project / ".env")
    with (project / ".env").open("a") as f:
        f.write("COMPOSE_FILE=docker-compose.yml:docker-compose.vault.yml:docker-compose.mqtt.yml\n")
    run(project, ["", "", "", "3", "n", "n", "n", "", *([""] * KEYS)])
    assert env(project)["COMPOSE_FILE"] == "docker-compose.yml:docker-compose.vault.yml"


def test_keys_can_live_in_your_own_vault_or_key_manager(project: Path) -> None:
    run(project, ["", "", "", "3", "n", "n", "n", "3", "https://vault.lan:8200", *([""] * KEYS)])
    e = env(project)
    assert e["WA_SECRETS_BACKEND"] == "vault" and e["WA_VAULT_ADDR"] == "https://vault.lan:8200"
    assert e["COMPOSE_FILE"] == "docker-compose.yml:docker-compose.vault.yml"
    run(project, ["", "", "", "3", "n", "n", "n", "4", "op read op://Home/{name}/credential", *([""] * KEYS)])
    e = env(project)
    again = run(project, ["", "", "", "3", "n", "n", "n", "4", "", *([""] * KEYS)])  # Enter keeps it, quotes and all
    assert "single quote" not in again and env(project)["WA_SECRETS_COMMAND"] == e["WA_SECRETS_COMMAND"]
    assert e["WA_SECRETS_BACKEND"] == "command" and e["WA_SECRETS_COMMAND"] == "'op read op://Home/{name}/credential'"


def test_the_bundled_openbao_is_set_up_after_the_stack_starts(project: Path) -> None:
    text = run(project, ["", "", "", "3", "n", "n", "n", "2", *([""] * KEYS)])
    assert "scripts/openbao.sh setup" in text and env(project)["WA_SECRETS_BACKEND"] == "files"

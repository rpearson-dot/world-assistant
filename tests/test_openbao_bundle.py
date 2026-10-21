import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app import config, secretsctl, secretstore

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch, tmp_path):  # type: ignore[no-untyped-def]
    for k in ("WA_SECRETS_BACKEND", "API_TOKEN", "TFL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    secretstore.clear_cache()
    yield
    secretstore.clear_cache()


def test_local_values_excludes_bootstrap_and_db_secrets(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "api_token.txt").write_text("tok\n")
    (tmp_path / "tfl_app_key").write_text("k")
    (tmp_path / "postgres_password.txt").write_text("pw")
    (tmp_path / "vault_token.txt").write_text("t")
    (tmp_path / "vault_secret_id.txt").write_text("s")
    vals = secretstore.local_values(tmp_path)
    assert vals == {"api_token": "tok", "tfl_app_key": "k"}


def test_dump_json_refuses_terminal(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdout", Tty())
    rc = secretsctl.main(["dump-json"])
    assert rc != 0


def test_dump_json_outputs_json(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "api_token.txt").write_text("tok")
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = secretsctl.main(["dump-json"])
    assert rc == 0
    assert json.loads(buf.getvalue()) == {"api_token": "tok"}


def test_openbao_compose_is_valid_and_private() -> None:
    doc = yaml.safe_load((ROOT / "docker-compose.openbao.yml").read_text())
    svc = doc["services"]["openbao"]
    assert "ports" not in svc
    assert svc["entrypoint"] == ["bao"]  # the image's own entrypoint chowns and fails on Unraid shares
    assert svc["image"].startswith("openbao/openbao:")
    un = doc["services"]["openbao-unseal"]
    assert "ports" not in un
    assert any("/run/unseal_key" in v and v.endswith(":ro") for v in un["volumes"])


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("script", ["scripts/openbao.sh", "scripts/deploy.sh"])
def test_scripts_parse(script: str) -> None:
    subprocess.run(["bash", "-n", str(ROOT / script)], check=True)

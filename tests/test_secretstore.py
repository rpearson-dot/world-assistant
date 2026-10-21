import json
import sys

import httpx
import pytest

from app import config, secretsctl, secretstore


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch, tmp_path):  # type: ignore[no-untyped-def]
    for k in ("WA_SECRETS_BACKEND", "WA_VAULT_ADDR", "WA_SECRETS_COMMAND", "API_TOKEN", "VAULT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    secretstore.clear_cache()
    yield
    secretstore.clear_cache()


def test_txt_suffix_is_found(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "api_token.txt").write_text(" abc \n")
    assert secretstore.resolve("api_token", tmp_path) == ("abc", "file")


def test_plain_name_wins_over_txt(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "api_token").write_text("plain")
    (tmp_path / "api_token.txt").write_text("txt")
    assert secretstore.read_secret("api_token", tmp_path) == "plain"


def test_empty_file_falls_through_to_env(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "api_token.txt").write_text("")
    monkeypatch.setenv("API_TOKEN", "envval")
    assert secretstore.resolve("api_token", tmp_path) == ("envval", "env")


def _vault(monkeypatch, handler):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WA_SECRETS_BACKEND", "vault")
    monkeypatch.setenv("WA_VAULT_ADDR", "http://vault.test:8200")
    monkeypatch.setattr(
        secretstore.VaultBackend, "_client",
        lambda self: httpx.Client(transport=httpx.MockTransport(handler)),
    )  # fmt: skip


def test_vault_token_reads_kv2_document(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.url.path == "/v1/secret/data/world-assistant"
        assert req.headers["X-Vault-Token"] == "tok"
        return httpx.Response(200, json={"data": {"data": {"api_token": "from-vault", "tfl_app_key": "k"}}})

    _vault(monkeypatch, handler)
    (tmp_path / "vault_token").write_text("tok")
    (tmp_path / "api_token").write_text("from-file")
    assert secretstore.resolve("api_token", tmp_path) == ("from-vault", "vault")
    assert secretstore.read_secret("tfl_app_key", tmp_path) == "k"
    assert len(calls) == 1  # one document, fetched once and cached


def test_vault_missing_field_falls_back_to_file(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _vault(monkeypatch, lambda r: httpx.Response(200, json={"data": {"data": {}}}))
    (tmp_path / "vault_token").write_text("tok")
    (tmp_path / "mqtt_password").write_text("filepw")
    assert secretstore.resolve("mqtt_password", tmp_path) == ("filepw", "file")


def test_vault_approle_login(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/auth/approle/login":
            assert json.loads(req.content) == {"role_id": "r", "secret_id": "s"}
            return httpx.Response(200, json={"auth": {"client_token": "t2", "lease_duration": 3600}})
        assert req.headers["X-Vault-Token"] == "t2"
        return httpx.Response(200, json={"data": {"data": {"api_token": "x"}}})

    _vault(monkeypatch, handler)
    (tmp_path / "vault_role_id").write_text("r")
    (tmp_path / "vault_secret_id").write_text("s")
    assert secretstore.read_secret("api_token", tmp_path) == "x"


def test_vault_outage_falls_back_to_file(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    _vault(monkeypatch, handler)
    (tmp_path / "vault_token").write_text("tok")
    (tmp_path / "api_token").write_text("fallback")
    assert secretstore.resolve("api_token", tmp_path) == ("fallback", "file")


def test_vault_outage_after_success_keeps_last_values(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    state = {"up": True}

    def handler(req: httpx.Request) -> httpx.Response:
        if not state["up"]:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"data": {"data": {"api_token": "v1"}}})

    _vault(monkeypatch, handler)
    monkeypatch.setenv("WA_SECRETS_CACHE_SECONDS", "0")
    (tmp_path / "vault_token").write_text("tok")
    assert secretstore.read_secret("api_token", tmp_path) == "v1"
    state["up"] = False
    assert secretstore.read_secret("api_token", tmp_path) == "v1"


def test_command_backend(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WA_SECRETS_BACKEND", "command")
    monkeypatch.setenv(
        "WA_SECRETS_COMMAND",
        f"{sys.executable} -c \"import sys; print('val-' + sys.argv[1])\" {{name}}",
    )
    assert secretstore.resolve("tfl_app_key", tmp_path) == ("val-tfl_app_key", "command")


def test_command_failure_falls_back(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WA_SECRETS_BACKEND", "command")
    monkeypatch.setenv("WA_SECRETS_COMMAND", f"{sys.executable} -c \"raise SystemExit(1)\" {{name}}")
    (tmp_path / "tfl_app_key").write_text("fromfile")
    assert secretstore.resolve("tfl_app_key", tmp_path) == ("fromfile", "file")


def test_unknown_backend_is_not_fatal(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WA_SECRETS_BACKEND", "nonsense")
    (tmp_path / "api_token").write_text("t")
    assert secretstore.read_secret("api_token", tmp_path) == "t"


def test_report_and_cli_never_print_values(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "api_token").write_text("SUPERSECRETVALUE")
    rows = secretstore.report(tmp_path)
    assert "SUPERSECRETVALUE" not in json.dumps(rows)
    assert secretsctl.main(["check"]) == 1  # postgres_password missing
    out = capsys.readouterr().out
    assert "SUPERSECRETVALUE" not in out and "MISSING" in out
    (tmp_path / "postgres_password").write_text("p")
    assert secretsctl.main(["check"]) == 0

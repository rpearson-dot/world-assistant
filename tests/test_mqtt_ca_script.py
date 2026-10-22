"""scripts/mqtt-ca.sh: a private CA and a broker certificate that World Assistant's TLS check accepts."""

from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from app.mqtt_config import tls_context

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")


def run(project: Path, *args: str) -> str:
    r = subprocess.run(["bash", "scripts/mqtt-ca.sh", *args], cwd=project, capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "mqtt-ca.sh", tmp_path / "scripts")
    (tmp_path / ".env").write_text("WA_DATA_DIR=./data\nWA_SECRETS_DIR=./secrets\n# WA_MQTT_TLS_CA=\n")
    return tmp_path


def handshake(certs: Path, ca: Path | None, name: str) -> None:
    """A TLS server on this certificate, and our client checking it as World Assistant does."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certs / "fullchain.pem", certs / "privkey.pem")
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve() -> None:
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True) as s:
                s.recv(1)
        except (ssl.SSLError, OSError):
            pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    ctx = tls_context(True, str(ca) if ca else "")
    assert ctx is not None
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw, ctx.wrap_socket(raw, server_hostname=name):
            pass
    finally:
        listener.close()


def test_a_certificate_for_every_name_trusted_through_the_ca(project: Path) -> None:
    out = run(project, "mqtt.example.com", "127.0.0.1", "localhost")
    certs, ca = project / "data/mosquitto/certs", project / "data/keys/mqtt-ca.pem"
    assert "created  the CA" in out and (project / "secrets/mqtt-ca/ca.key").stat().st_mode & 0o777 == 0o600
    assert ca.read_text() == (certs / "ca.pem").read_text() and "PRIVATE" not in ca.read_text()
    assert "WA_MQTT_TLS_CA=/keys/mqtt-ca.pem" in (project / ".env").read_text()
    handshake(certs, ca, "127.0.0.1")
    handshake(certs, ca, "localhost")
    with pytest.raises(ssl.SSLCertVerificationError, match="mismatch"):
        handshake(certs, ca, "other.example.com")  # not one of the names
    with pytest.raises(ssl.SSLCertVerificationError, match="self-signed|unable to get"):
        handshake(certs, None, "127.0.0.1")  # without the CA, not trusted


def test_renewing_keeps_the_ca_so_clients_need_no_change(project: Path) -> None:
    run(project, "127.0.0.1")
    ca_before = (project / "secrets/mqtt-ca/ca.pem").read_text()
    cert_before = (project / "data/mosquitto/certs/fullchain.pem").read_text()
    out = run(project, "127.0.0.1", "localhost")
    assert "kept     the CA" in out and (project / "secrets/mqtt-ca/ca.pem").read_text() == ca_before
    assert (project / "data/mosquitto/certs/fullchain.pem").read_text() != cert_before
    handshake(project / "data/mosquitto/certs", project / "data/keys/mqtt-ca.pem", "localhost")  # old CA, new cert


def test_bad_names_are_refused(project: Path) -> None:
    r = subprocess.run(["bash", "scripts/mqtt-ca.sh", "not a name"], cwd=project, capture_output=True, text=True, check=False)
    assert r.returncode == 2 and "not a host name" in r.stderr

import pytest

from app import config, secretstore
from app.config import Settings


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch, tmp_path):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    secretstore.clear_cache()
    yield
    secretstore.clear_cache()


def test_password_is_added_and_escaped(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "redis_password.txt").write_text("p@ss/w:rd\n")
    s = Settings(redis_url="redis://wa-redis:6379/0")
    assert s.redis_dsn == "redis://:p%40ss%2Fw%3Ard@wa-redis:6379/0"


def test_no_secret_leaves_url_alone() -> None:
    assert Settings(redis_url="redis://10.0.0.5:6379/2").redis_dsn == "redis://10.0.0.5:6379/2"


def test_password_already_in_url_wins(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "redis_password.txt").write_text("fromsecret")
    s = Settings(redis_url="redis://:inurl@host:6379/0")
    assert s.redis_dsn == "redis://:inurl@host:6379/0"


def test_default_url_without_port(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "redis_password.txt").write_text("x")
    assert Settings(redis_url="redis://wa-redis/1").redis_dsn == "redis://:x@wa-redis/1"

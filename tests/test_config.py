from app import config


def test_read_secret_prefers_file_then_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    (tmp_path / "nh_subscription_key").write_text("  from-file\n")
    monkeypatch.setenv("NH_SUBSCRIPTION_KEY", "from-env")
    assert config.read_secret("nh_subscription_key") == "from-file"

    (tmp_path / "nh_subscription_key").unlink()
    assert config.read_secret("nh_subscription_key") == "from-env"

    monkeypatch.delenv("NH_SUBSCRIPTION_KEY")
    assert config.read_secret("nh_subscription_key", "fallback") == "fallback"


def test_database_url_uses_secret_password(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    (tmp_path / "postgres_password").write_text("s3cret\n")

    url = config.Settings().database_url
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "wa-db"
    assert url.password == "s3cret"
    assert "s3cret" not in repr(url)  # SQLAlchemy masks the password in repr

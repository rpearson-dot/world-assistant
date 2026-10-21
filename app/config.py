"""Application settings.

Plain settings come from WA_* environment variables. Secrets come from one place,
app/secretstore.py: an optional key vault, then files under /run/secrets, then the
environment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

from app import secretstore

SECRETS_DIR = Path(os.environ.get("WA_SECRETS_DIR", "/run/secrets"))


def read_secret(name: str, default: str = "") -> str:
    """Return the secret called `name` from the configured key vault, the secrets directory
    (<name> or <name>.txt) or the upper-cased environment variable. See app/secretstore.py."""
    return secretstore.read_secret(name, SECRETS_DIR, default)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WA_", extra="ignore")

    env: str = "production"
    log_level: str = "INFO"

    db_host: str = "wa-db"
    db_port: int = 5432
    db_name: str = "worldassistant"
    db_user: str = "worldassistant"

    # Shared Redis: every key this app writes is prefixed "wa:".
    redis_url: str = "redis://wa-redis:6379/0"

    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_enabled: bool = True
    mqtt_topic_prefix: str = "world_assistant"
    mqtt_discovery_prefix: str = "homeassistant"
    # Home Assistant sensors: the worker reads this API and publishes the results over MQTT.
    ha_publish: bool = True
    ha_api_url: str = "http://api:8000"
    ha_interval_min: int = 5
    # Extra places Home Assistant can ask for events at (on top of its home zone). Each place costs
    # (24 / refresh) searches a day on every events source, so the number is capped and they time out.
    ha_locations_max: int = 8
    ha_location_ttl_days: int = 30  # a place not renewed for this long is dropped
    ha_location_pressure_ttl_days: int = 7  # the shorter time once 75% of the cap is used
    ha_location_refresh_h: int = 12  # how often each place's events are searched again
    ha_location_radius_km: float = 8.0
    ha_location_days: int = 7

    # National Highways allows 10 calls per subscription key per minute.
    # Stay under it so retries and clock skew never trip the upstream limit.
    nh_rate_limit_per_minute: int = 8
    nh_base_url: str = "https://api.data.nationalhighways.co.uk"

    # Raw-response capture (temporary: used to learn real payload shapes).
    nh_capture_enabled: bool = False
    nh_capture_retention_days: int = 3
    # Live polling of closures, speed limits and signs, stored in Postgres.
    nh_poll_enabled: bool = False
    local_tz: str = "Europe/London"
    bus_poll_enabled: bool = True
    ticketmaster_base_url: str = ""  # empty = https://app.ticketmaster.com/discovery/v2
    skiddle_base_url: str = ""  # empty = https://www.skiddle.com/api/v1
    pollen_base_url: str = ""  # empty = https://www.polleninformation.at/api/forecast/public
    pollen_country: str = "GB"  # two-letter country code the pollen service is asked about
    pollen_grid_step_deg: float = 1.0  # spacing of the nationwide pollen lattice (0.25 to 3)
    nr_base_url: str = ""  # National Rail (Rail Data Marketplace) base URL; empty = the default
    tfl_naive_tz: str = "Europe/London"  # zone for TfL times that carry no offset
    valhalla_url: str = "http://valhalla:8002"
    fuel_poll_enabled: bool = False
    fuel_price_interval_min: int = 30
    nh_live_interval_min: int = 10
    nh_planned_interval_hours: int = 2
    nh_planned_days: int = 14
    nh_planned_max_pages: int = 40
    # lower-left lat, lower-left lon, upper-right lat, upper-right lon (WGS84).
    # A rough box around M25 J10 -> M40 -> M42 -> M6. Tighten once we have data.
    nh_vms_bbox: str = "51.20,-2.05,52.60,-0.30"

    @property
    def db_password(self) -> str:
        return read_secret("postgres_password")

    @property
    def api_token(self) -> str:
        return read_secret("api_token")

    @property
    def tfl_app_key(self) -> str:
        """Optional: TfL allows a low unauthenticated rate, so an empty key still works."""
        return read_secret("tfl_app_key")

    @property
    def fuel_finder_client_id(self) -> str:
        return read_secret("fuel_finder_client_id")

    @property
    def fuel_finder_client_secret(self) -> str:
        return read_secret("fuel_finder_client_secret")

    @property
    def nr_access_token(self) -> str:
        return read_secret("nr_access_token")

    @property
    def ticketmaster_api_key(self) -> str:
        return read_secret("ticketmaster_api_key")

    @property
    def skiddle_api_key(self) -> str:
        return read_secret("skiddle_api_key")

    @property
    def redis_dsn(self) -> str:
        """redis_url with the redis_password secret added, unless the URL already carries a password."""
        pw = read_secret("redis_password")
        parts = urlsplit(self.redis_url)
        if not pw or parts.password or parts.scheme not in ("redis", "rediss"):
            return self.redis_url
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = f":{quote(pw, safe='')}@{host}" + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    @property
    def pollen_api_key(self) -> str:
        return read_secret("pollen_api_key")

    @property
    def mqtt_password(self) -> str:
        return read_secret("mqtt_password")

    @property
    def nh_primary_key(self) -> str:
        return read_secret("nh_subscription_key")

    @property
    def nh_secondary_key(self) -> str:
        return read_secret("nh_subscription_key_secondary")

    @property
    def database_url(self) -> URL:
        # psycopg 3 serves both the async app engine and the sync Alembic engine.
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

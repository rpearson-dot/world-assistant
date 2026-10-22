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

    # Requests from the local network need no token (app/localnet.py). WA_LOCAL_HOSTS adds host names that
    # count as home ones (comma separated), beyond IP addresses, single-label names and .lan/.local and so on.
    local_no_token: bool = True
    local_hosts: str = ""

    db_host: str = "wa-db"
    db_port: int = 5432
    db_name: str = "worldassistant"
    db_user: str = "worldassistant"

    # Shared Redis: every key this app writes is prefixed "wa:".
    redis_url: str = "redis://wa-redis:6379/0"

    # The MQTT broker Home Assistant uses. Empty: nothing is published (Home Assistant is optional).
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_publish_port: int = 1883  # the bundled broker's port on this server, shown on the /ha page
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
    ha_fuel_max_age_h: float = 720.0  # fuel prices up to this old are published; the dashboards show each price's age
    ha_routes_max: int = 12  # trips (one direction each) Home Assistant may add; they are routed locally, so no provider cost

    # The map page. Tiles are the one thing a browser fetches from outside; point these at your own
    # tile server to stop that. The attribution is shown on the map and the licence requires it.
    map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    map_tile_attribution: str = "© OpenStreetMap contributors"
    map_max_zoom: int = 19

    # The knowledge graph (optional, docker-compose.graph.yml). Neo4j holds relationships derived
    # from Postgres and owns no data of its own: it can be dropped and rebuilt at any time.
    graph_enabled: bool = False
    graph_url: str = "bolt://wa-graph:7687"
    graph_user: str = "neo4j"
    graph_near_km: float = 3.0  # how close a station or location must be to a place to be "near" it
    graph_along_km: float = 2.0  # how close a station must be to a corridor to be "along" it

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
    bus_live_enabled: bool = True  # GPS positions of followed buses (needs the BODS key), inside the journey window only
    ticketmaster_base_url: str = ""  # empty = https://app.ticketmaster.com/discovery/v2
    skiddle_base_url: str = ""  # empty = https://www.skiddle.com/api/v1
    pollen_base_url: str = ""  # empty = https://www.polleninformation.at/api/forecast/public
    pollen_country: str = "GB"  # two-letter country code the pollen service is asked about
    pollen_grid_step_deg: float = 1.0  # spacing of the nationwide pollen lattice (0.25 to 3)
    # The provider allows 40 requests a day per key. Everything draws from one budget below that.
    pollen_daily_limit: int = 36
    pollen_grid_calls_per_run: int = 12  # the grid job runs twice a day: about every point every two days
    pollen_reserve_calls: int = 6  # kept back from the grid for saved places and live look-ups
    nr_base_url: str = ""  # National Rail (Rail Data Marketplace) base URL; empty = the default
    tfl_naive_tz: str = "Europe/London"  # zone for TfL times that carry no offset
    valhalla_url: str = "http://valhalla:8002"
    fuel_poll_enabled: bool = False
    codepoint_poll_enabled: bool = False  # the compose file turns it on; CI and tests leave it off
    codepoint_url: str = "https://api.os.uk/downloads/v1/products/CodePointOpen/downloads?area=GB&format=CSV&redirect"
    tflroads_poll_enabled: bool = False  # the compose file turns it on; CI and tests leave it off
    energy_poll_enabled: bool = False  # the compose file turns it on; CI and tests leave it off
    octopus_account: str = ""  # e.g. A-1234ABCD; with the octopus_api_key secret this finds the tariff
    octopus_tariff: str = ""  # optional override, e.g. E-1R-COSY-22-12-08-A; needs no key
    flood_poll_enabled: bool = False  # the compose file turns it on; CI and tests leave it off
    naptan_poll_enabled: bool = False  # the compose file turns it on; CI and tests leave it off
    naptan_url: str = "https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv"
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
    def octopus_api_key(self) -> str:
        return read_secret("octopus_api_key")

    @property
    def bods_api_key(self) -> str:
        return read_secret("bods_api_key")

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
    def mqtt_active(self) -> bool:
        """Publishing to Home Assistant is on: a broker is set and nothing turned it off."""
        return self.mqtt_enabled and bool(self.mqtt_host.strip())

    @property
    def mqtt_password(self) -> str:
        return read_secret("mqtt_password")

    @property
    def graph_password(self) -> str:
        return read_secret("graph_password")

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

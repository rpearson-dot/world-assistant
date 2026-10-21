from fastapi.testclient import TestClient

from app.main import app


class FakeConn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *_args, **_kwargs):
        return None


class FakeEngine:
    def connect(self):
        return FakeConn()


class FakeRedis:
    async def ping(self):
        return True


class BrokenRedis:
    async def ping(self):
        raise ConnectionError("down")


# No `with` block: the lifespan (which would open real connections) is skipped.
client = TestClient(app)


def test_healthz_never_touches_dependencies():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_ok_when_dependencies_up():
    app.state.engine = FakeEngine()
    app.state.redis = FakeRedis()
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"] == {"postgres": "ok", "redis": "ok"}


def test_readyz_503_when_redis_down():
    app.state.engine = FakeEngine()
    app.state.redis = BrokenRedis()
    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["redis"].startswith("error")

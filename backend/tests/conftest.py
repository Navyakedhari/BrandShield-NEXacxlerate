import os

# Test configuration only: throwaway values, never real secrets.
os.environ.update(
    {
        "APP_ENV": "test",
        "JWT_SECRET": "test-only-secret-not-for-real-use-0123456789",
        "ADMIN_EMAIL": "admin@example.com",
        "ADMIN_PASSWORD": "Admin-Pass-for-tests-1",
        "ADMIN_NAME": "Test Admin",
        "DATABASE_URL": "sqlite://",
        "SCAN_EXECUTOR": "inline",  # scans run synchronously in tests (no threads, no Redis)
        "SCHEDULER_ENABLED": "false",
    }
)

# A real key in the shell must never reach a test run.
for _name in ("GOOGLE_SAFE_BROWSING_API_KEY", "TAVILY_API_KEY", "SEARXNG_BASE_URL", "GEMINI_API_KEY", "HUGGINGFACE_API_TOKEN", "REDIS_URL", "SCAN_INTERVAL_MINUTES", "SENTRY_DSN"):
    os.environ.pop(_name, None)

# Optional: run the whole suite against PostgreSQL. Set TEST_DATABASE_URL (a throwaway database, never a real one) and every test engine
# that would be in-memory SQLite becomes its own random schema in that database, dropped when the run ends. File-based SQLite engines
# (migration tests, the seed race test) stay SQLite on purpose. See tests/pg_support.py.
import app.db.session as _session  # noqa: E402
from tests import pg_support  # noqa: E402

pg_support.install(_session)

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.config.security as _security  # noqa: E402
from app.config.config import Settings  # noqa: E402
from app.config.security import hash_password  # noqa: E402
from app.core.pipeline.url_safety import CheckResult  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import make_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import User  # noqa: E402

_security.BCRYPT_ROUNDS = 4  # fast hashing in tests only

ADMIN = ("admin@example.com", "Admin-Pass-for-tests-1")


class FakeChecker:
    """Stands in for the network URL checker. Records calls; result is configurable."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.result = CheckResult("verified", "URL is reachable", 200)

    def check(self, url: str) -> CheckResult:
        self.calls.append(url)
        return self.result


@pytest.fixture
def settings(tmp_path) -> Settings:
    # _env_file=None: a developer's real server/.env (e.g. TAVILY_API_KEY) must not leak into tests
    return Settings(_env_file=None, data_dir=tmp_path / "data", database_url="sqlite://")  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """Safety net: any real outbound HTTP or DNS lookup in a test is a bug. MockTransport is unaffected."""
    import socket

    def blocked(*_a, **_k):
        raise AssertionError("live network access attempted in a test")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def checker() -> FakeChecker:
    return FakeChecker()


@pytest.fixture
def app(settings, checker):
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return create_app(settings, engine=engine, url_checker=checker)  # type: ignore[arg-type]


@pytest.fixture
def client(app):
    with TestClient(app) as c:  # runs lifespan: seeds the admin
        yield c


@pytest.fixture
def db(app):
    with app.state.session_factory() as session:
        yield session


def login(client: TestClient, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def headers_for(client: TestClient, email: str, password: str) -> dict:
    r = login(client, email, password)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_user(db, email: str, role: str, password: str = "Some-Password-1", active: bool = True) -> User:
    u = User(name=email.split("@")[0].title(), email=email, password_hash=hash_password(password), role=role, active=active)
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def admin_h(client) -> dict:
    return headers_for(client, *ADMIN)


@pytest.fixture
def analyst_h(client, db) -> dict:
    make_user(db, "analyst@example.com", "analyst")
    return headers_for(client, "analyst@example.com", "Some-Password-1")


@pytest.fixture
def viewer_h(client, db) -> dict:
    make_user(db, "viewer@example.com", "viewer")
    return headers_for(client, "viewer@example.com", "Some-Password-1")


@pytest.fixture
def brand(client, admin_h) -> dict:
    r = client.post("/api/brands", json={"name": "Acme Bank", "aliases": ["Acme"]}, headers=admin_h)
    assert r.status_code == 201, r.text
    return r.json()

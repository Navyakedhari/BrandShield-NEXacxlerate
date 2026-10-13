"""/health, /health/live, /api/health/details: every component state. No network, no real Redis (fakes)."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from alembic.config import Config
from pydantic import SecretStr

from alembic import command
from app.config.time import utcnow
from app.db.session import get_db, make_engine, make_session_factory
from app.models import CollectorState
from app.services import health as svc
from tests.conftest import headers_for
from tests.db.test_migration import SERVER
from tests.scan_helpers import make_client, set_mode

REDIS_URL = "redis://:s3cr3t-pw-value@redis.internal:6379/0"


def components(client) -> dict:
    return client.get("/health").json()["components"]


# --------------------------------------------------------------------------------------------- public shape


def test_public_health_is_minimal_and_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] == {"status": "ok"}
    assert set(body) == {"status", "version", "database", "components"}
    assert body["components"]["database"] == "ok"
    assert body["components"]["redis"] == "not_configured" and body["components"]["worker"] == "not_configured"
    assert body["components"]["data_dir"] == "ok" and body["components"]["collectors"] == "ok"
    assert client.get("/api/health").json() == body


def test_liveness_touches_nothing(app, client):
    app.dependency_overrides[get_db] = lambda: (_ for _ in ()).throw(RuntimeError("db gone"))
    r = client.get("/health/live")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_details_requires_admin(client, admin_h, analyst_h, viewer_h):
    assert client.get("/api/health/details").status_code == 401
    assert client.get("/api/health/details", headers=viewer_h).status_code == 403
    r = client.get("/api/health/details", headers=admin_h)
    assert r.status_code == 200
    body = r.json()
    assert set(body["checks"]) == {"database", "migration", "collectors", "redis", "worker", "data_dir"}
    assert body["checks"]["collectors"]["sources"]["sample"]["status"] == "ok"
    assert body["checks"]["data_dir"]["writable"] is True


# --------------------------------------------------------------------------------------------- database


def test_database_down_is_503_and_down(app, client):
    class Broken:
        def execute(self, *_a, **_k):
            raise RuntimeError("password=hunter2 db gone")

        def rollback(self):
            pass

    app.dependency_overrides[get_db] = lambda: Broken()
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "down" and body["components"]["database"] == "down"
    assert "hunter2" not in r.text  # the exception text never leaves


# --------------------------------------------------------------------------------------------- redis and worker


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def ping(self) -> bool:
        if self.fail:
            raise ConnectionError(f"cannot connect to {REDIS_URL}")
        return True


def redis_client(settings, checker, fake, monkeypatch=None):
    s = settings.model_copy(update={"redis_url": SecretStr(REDIS_URL)})
    return make_client(s, checker)


def test_redis_ok_and_down_never_leak_url(settings, checker, monkeypatch):
    with redis_client(settings, checker, None) as c:
        c.app_ref.state.redis_factory = lambda _s: FakeRedis()
        monkeypatch.setattr(svc, "check_worker", lambda *_a, **_k: {"status": "ok", "workers": 1})
        assert c.get("/health").json()["components"]["redis"] == "ok"
        c.app_ref.state.redis_factory = lambda _s: FakeRedis(fail=True)
        r = c.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "degraded" and r.json()["components"]["redis"] == "down"
        admin_h = headers_for(c, "admin@example.com", "Admin-Pass-for-tests-1")
        details = c.get("/api/health/details", headers=admin_h)
        for text in (r.text, details.text):
            assert "s3cr3t-pw-value" not in text and "redis.internal" not in text
        assert details.json()["checks"]["redis"] == {"status": "down", "error": "ConnectionError"}


def test_redis_not_configured_when_url_unset(settings):
    assert svc.check_redis(settings) == {"status": "not_configured", "note": "REDIS_URL not set: scans run in the API process"}
    assert svc.check_worker(settings)["status"] == "not_configured"


def test_worker_states_from_rq_registry(settings, monkeypatch):
    import rq

    s = settings.model_copy(update={"redis_url": SecretStr(REDIS_URL)})
    state: dict = {"workers": []}

    class FakeQueue:
        def __init__(self, name, connection=None):
            self.count = 3

    monkeypatch.setattr(rq, "Queue", FakeQueue)
    monkeypatch.setattr(rq.Worker, "all", staticmethod(lambda queue=None, **_k: state["workers"]))
    # no worker registered: scans would queue forever
    r = svc.check_worker(s, lambda _s: object())
    assert r["status"] == "degraded" and r["workers"] == 0 and r["queued"] == 3
    # one live worker with a fresh heartbeat
    state["workers"] = [SimpleNamespace(last_heartbeat=utcnow() - timedelta(seconds=7))]
    r = svc.check_worker(s, lambda _s: object())
    assert r["status"] == "ok" and r["workers"] == 1 and 5 <= r["last_heartbeat_age_seconds"] <= 30

    def boom(*_a, **_k):
        raise ConnectionError(REDIS_URL)

    monkeypatch.setattr(rq.Worker, "all", staticmethod(boom))
    r = svc.check_worker(s, lambda _s: object())
    assert r["status"] == "degraded" and REDIS_URL not in json.dumps(r)


# --------------------------------------------------------------------------------------------- data dir


def test_data_dir_writable_and_probe_file_removed(settings):
    r = svc.check_data_dir(settings)
    assert r["status"] == "ok" and r["writable"] is True
    assert not list(settings.data_dir.glob(".health-*"))


def test_data_dir_not_writable_is_degraded_overall(settings, checker, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    bad = settings.model_copy(update={"data_dir": blocker / "data"})  # a path below a regular file can never be created
    assert svc.check_data_dir(bad)["status"] == "down"
    with make_client(bad, checker) as c:
        r = c.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "degraded" and r.json()["components"]["data_dir"] == "down"


def test_data_dir_low_disk_is_degraded(settings, monkeypatch):
    monkeypatch.setattr(svc.shutil, "disk_usage", lambda _p: SimpleNamespace(free=10 * 1024 * 1024))
    r = svc.check_data_dir(settings)
    assert r["status"] == "degraded" and r["free_mb"] == 10


# --------------------------------------------------------------------------------------------- migration


def _upgrade(url: str, rev: str) -> None:
    cfg = Config(str(SERVER / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, rev)


def test_migration_at_head_behind_and_unknown(settings, tmp_path):
    url = f"sqlite:///{(tmp_path / 'mig.db').as_posix()}"
    _upgrade(url, "head")
    sf = make_session_factory(make_engine(url))
    with sf() as db:
        r = svc.check_migration(db, settings)
        assert r["status"] == "ok" and r["current"] == r["expected"] == "0007"
    # an older database
    url2 = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    _upgrade(url2, "0003")
    with make_session_factory(make_engine(url2))() as db:
        r = svc.check_migration(db, settings)
        assert r["status"] == "degraded" and r["current"] == "0003" and r["expected"] == "0007"


def test_migration_table_missing_unknown_outside_production_degraded_in_production(settings, app):
    with app.state.session_factory() as db:  # schema made by create_all: no alembic_version table
        assert svc.check_migration(db, settings)["status"] == "unknown"
        prod = settings.model_copy(update={"app_env": "production"})
        assert svc.check_migration(db, prod)["status"] == "degraded"


def test_migration_scripts_missing_is_unknown(settings, tmp_path):
    url = f"sqlite:///{(tmp_path / 'm2.db').as_posix()}"
    _upgrade(url, "head")
    with make_session_factory(make_engine(url))() as db:
        assert svc.check_migration(db, settings, head=None)["status"] == "unknown"


# --------------------------------------------------------------------------------------------- collectors


def collectors_of(client, admin_h) -> dict:
    return client.get("/api/health/details", headers=admin_h).json()["checks"]["collectors"]


def test_collectors_sample_mode_is_ok(client, admin_h):
    c = collectors_of(client, admin_h)
    assert c["status"] == "ok" and c["effective_mode"] == "sample" and list(c["sources"]) == ["sample"]


def test_collectors_live_without_key_is_degraded_with_reason(settings, checker):
    with make_client(settings, checker) as c:
        h = headers_for(c, "admin@example.com", "Admin-Pass-for-tests-1")
        set_mode(c, h, "live")
        d = collectors_of(c, h)
        assert d["status"] == "degraded"
        assert d["sources"]["tavily"]["configured"] is False and d["sources"]["tavily"]["status"] == "degraded"
        assert d["sources"]["searxng"]["status"] == "not_configured"  # optional, switched off
        assert d["sources"]["itunes"]["status"] == "ok"
        assert c.get("/health").json()["components"]["collectors"] == "degraded"
        assert c.get("/health").status_code == 200


def test_collectors_live_with_key_ok_then_stored_failure_degrades_without_leaks(settings, checker):
    key = "tvly-super-secret-key-123456"
    s = settings.model_copy(update={"tavily_api_key": SecretStr(key), "searxng_base_url": "https://user:pw0rd@search.internal/"})
    with make_client(s, checker) as c:
        h = headers_for(c, "admin@example.com", "Admin-Pass-for-tests-1")
        set_mode(c, h, "live")
        d = collectors_of(c, h)
        assert d["sources"]["tavily"] == {"configured": True, "active": True, "status": "ok"}
        assert d["sources"]["searxng"]["configured"] is True
        now = utcnow()
        with c.app_ref.state.session_factory() as db:
            db.add(CollectorState(collector="tavily", status="unavailable", reason="quota", message="monthly budget used",
                                  last_attempt_at=now - timedelta(minutes=5), last_success_at=now - timedelta(days=1), consecutive_failures=3))
            db.commit()
        resp = c.get("/api/health/details", headers=h)
        d = resp.json()["checks"]["collectors"]
        t = d["sources"]["tavily"]
        assert t["status"] == "degraded" and t["reason"] == "quota" and t["consecutive_failures"] == 3
        assert 250 <= t["last_attempt_age_seconds"] <= 400 and t["last_success_age_seconds"] >= 86000
        assert d["status"] == "degraded"  # one of the live collectors is down, the others work
        for text in (resp.text, c.get("/health").text):
            assert key not in text and "pw0rd" not in text and "search.internal" not in text


def test_collectors_google_play_package_missing_and_present(db, settings):
    live = settings.model_copy(update={"tavily_api_key": SecretStr("tvly-x-123456")})
    from app.services import data_mode
    from tests.conftest import make_user

    admin = make_user(db, "a2@example.com", "admin")
    data_mode.set_data_mode(db, "live", admin)
    missing = svc.check_collectors(db, live, google_play_installed=lambda: False)["sources"]["google_play"]
    assert missing["configured"] is False and missing["status"] == "degraded"
    present = svc.check_collectors(db, live, google_play_installed=lambda: True)["sources"]["google_play"]
    assert present["configured"] is True and present["status"] == "ok"


def test_all_live_collectors_failing_still_not_down(db, settings):
    from app.services import data_mode
    from tests.conftest import make_user

    data_mode.set_data_mode(db, "live", make_user(db, "a3@example.com", "admin"))
    res = svc.check_collectors(db, settings, google_play_installed=lambda: False)  # no Tavily key either
    assert res["status"] == "degraded"  # never "down": the API itself is up and the sample/other paths exist


def test_overall_rules():
    ok = {"database": {"status": "ok"}, "redis": {"status": "not_configured"}, "migration": {"status": "unknown"}}
    assert svc.overall(ok) == "ok"
    assert svc.overall({**ok, "redis": {"status": "down"}}) == "degraded"
    assert svc.overall({**ok, "database": {"status": "down"}}) == "down"


@pytest.mark.parametrize("path", ["/health", "/api/health"])
def test_health_is_unauthenticated(client, path):
    assert client.get(path).status_code == 200

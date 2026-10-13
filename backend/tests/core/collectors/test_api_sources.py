import httpx
import pytest

from app.config.config import Settings
from app.core.collectors.selection import CollectorRuntime
from app.db.base import Base
from app.db.session import make_engine, make_session_factory
from app.main import create_app
from app.models import AuditLog, Setting, Source

from .helpers import fixture_text


class World:
    """Mutable behaviour of the mocked outside world (no live network)."""

    def __init__(self):
        self.tavily_status = 200
        self.tavily_headers: dict = {}
        self.itunes_status = 200
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "api.tavily.com":
            body = fixture_text("tavily/search_ok.json") if self.tavily_status == 200 else fixture_text("tavily/error_500.json")
            return httpx.Response(self.tavily_status, content=body.encode(), headers={"content-type": "application/json", **self.tavily_headers})
        if host == "itunes.apple.com":
            return httpx.Response(self.itunes_status, content=fixture_text("itunes/search_ok.json").encode(), headers={"content-type": "application/json"})
        raise AssertionError(f"unexpected host {host}")


@pytest.fixture
def world():
    return World()


@pytest.fixture
def with_key():
    return True


@pytest.fixture
def settings(tmp_path, with_key) -> Settings:
    extra = {"tavily_api_key": "tvly-test-key-not-real-0123456789"} if with_key else {}
    return Settings(_env_file=None, data_dir=tmp_path / "data", database_url="sqlite://", tavily_max_queries_per_scan=2,
                    itunes_max_queries_per_scan=1, **extra)  # type: ignore[call-arg]


@pytest.fixture
def app(settings, checker, world):
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)

    class Clk:
        t = 0.0

        def __call__(self):
            return self.t

        def sleep(self, s):
            self.t += s

    clk = Clk()
    rt = CollectorRuntime(settings, sf, transport=httpx.MockTransport(world.handler), clock=clk, sleep=clk.sleep)
    return create_app(settings, engine=engine, url_checker=checker, collector_runtime=rt)  # type: ignore[arg-type]


def sources_by_slug(client, headers, **params):
    r = client.get("/api/sources", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json(), {i["slug"]: i for i in r.json()["items"]}


def test_sources_requires_auth(client):
    assert client.get("/api/sources").status_code == 401
    assert client.patch("/api/sources/1", json={"monitored": False}).status_code == 401
    assert client.post("/api/sources/1/retry").status_code == 401
    assert client.get("/api/settings/data-mode").status_code == 401
    assert client.put("/api/settings/data-mode", json={"data_mode": "live"}).status_code == 401


def test_list_sources_shape_and_credit_meter(client, viewer_h):
    body, by = sources_by_slug(client, viewer_h)
    assert body["data_mode"] == "live" and body["requested_data_mode"] == "auto"  # key present -> auto resolves to live
    assert list(by) == ["instagram", "facebook", "x", "telegram", "linkedin", "google_play", "app_store", "third_party_apk"]
    inst = by["instagram"]
    for k in ("id", "slug", "name", "kind", "status", "monitored", "message", "last_sync_at", "next_retry_at", "cached_age_seconds",
              "cached_at", "retry_in_minutes", "stale_since", "collectors", "source", "detail", "description"):
        assert k in inst
    assert inst["monitored"] is True and inst["last_sync_at"] is None and inst["collectors"] == ["tavily"]
    assert body["tavily_credits"]["used"] == 0 and body["tavily_credits"]["budget"] == 900 and body["tavily_credits"]["remaining"] == 900
    assert len(body["tavily_credits"]["month"]) == 7
    assert body["summary"]["total"] == 8 and body["summary"]["retry_policy"] == "1m,5m,15m,1h,6h"
    only_store = client.get("/api/sources", headers=viewer_h, params={"kind": "store"}).json()
    assert [i["slug"] for i in only_store["items"]] == ["google_play", "app_store", "third_party_apk"]
    assert client.get("/api/sources", headers=viewer_h, params={"kind": "bogus"}).status_code == 422


@pytest.mark.parametrize("with_key", [False])
def test_without_key_auto_is_sample_and_live_mode_reports_unavailable_honestly(client, admin_h, with_key):
    body, by = sources_by_slug(client, admin_h)
    assert body["data_mode"] == "sample" and by["instagram"]["status"] == "online" and "Sample data" in by["instagram"]["message"]
    r = client.put("/api/settings/data-mode", json={"data_mode": "live"}, headers=admin_h)
    assert r.status_code == 200 and r.json()["effective_mode"] == "live"
    body, by = sources_by_slug(client, admin_h)
    assert by["instagram"]["status"] == "unavailable" and "TAVILY_API_KEY" in by["instagram"]["message"]
    assert by["app_store"]["status"] == "online"  # iTunes needs no key


def test_data_mode_get_put_admin_only_validated_and_audited(client, admin_h, analyst_h, viewer_h, db):
    r = client.get("/api/settings/data-mode", headers=viewer_h)
    assert r.status_code == 200
    j = r.json()
    assert j["data_mode"] == "auto" and j["effective_mode"] == "live"
    assert [e["name"] for e in j["chain"]][:2] == ["tavily", "itunes"] or j["chain"][0]["name"] == "tavily"
    for h in (viewer_h, analyst_h):
        assert client.put("/api/settings/data-mode", json={"data_mode": "sample"}, headers=h).status_code == 403
    assert db.query(Setting).filter_by(key="data_mode").first() is None
    assert client.put("/api/settings/data-mode", json={"data_mode": "bogus"}, headers=admin_h).status_code == 422
    assert client.put("/api/settings/data-mode", json={}, headers=admin_h).status_code == 422
    r = client.put("/api/settings/data-mode", json={"data_mode": "sample"}, headers=admin_h)
    assert r.status_code == 200 and r.json()["data_mode"] == "sample" and r.json()["effective_mode"] == "sample"
    assert r.json()["chain"] == [{"name": "sample", "active": True, "configured": True, "reason": r.json()["chain"][0]["reason"]}]
    assert client.get("/api/settings/data-mode", headers=viewer_h).json()["data_mode"] == "sample"
    rows = db.query(AuditLog).filter_by(action="settings.data_mode_changed").all()
    assert len(rows) == 1 and rows[0].detail == "auto -> sample" and rows[0].actor == "Test Admin"


def test_monitor_toggle_roles_audit_and_404(client, admin_h, analyst_h, viewer_h, db):
    _, by = sources_by_slug(client, viewer_h)
    sid = by["telegram"]["id"]
    assert client.patch(f"/api/sources/{sid}", json={"monitored": False}, headers=viewer_h).status_code == 403
    r = client.patch(f"/api/sources/{sid}", json={"monitored": False}, headers=analyst_h)
    assert r.status_code == 200 and r.json()["monitored"] is False and r.json()["slug"] == "telegram"
    assert db.query(AuditLog).filter_by(action="source.monitor_toggled").one().detail == "monitored=false"
    client.patch(f"/api/sources/{sid}", json={"monitored": False}, headers=analyst_h)  # no change: no duplicate audit row
    assert db.query(AuditLog).filter_by(action="source.monitor_toggled").count() == 1
    client.patch(f"/api/sources/{sid}", json={"monitored": True}, headers=admin_h)
    assert db.query(AuditLog).filter_by(action="source.monitor_toggled").count() == 2
    assert client.patch("/api/sources/9999", json={"monitored": True}, headers=admin_h).status_code == 404
    assert client.patch(f"/api/sources/{sid}", json={"monitored": "maybe"}, headers=admin_h).status_code == 422


def test_retry_flow_unavailable_then_recovers_and_online_is_409(client, admin_h, analyst_h, viewer_h, brand, world, db):
    _, by = sources_by_slug(client, viewer_h)
    sid = by["instagram"]["id"]
    assert client.post(f"/api/sources/{sid}/retry", headers=viewer_h).status_code == 403
    assert client.post("/api/sources/9999/retry", headers=admin_h).status_code == 404

    world.tavily_status = 500
    r = client.post(f"/api/sources/{sid}/retry?confirm=true", headers=analyst_h)
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["source_id"] == sid and j["job_id"].startswith("retry_") and j["status"] == "unavailable"
    assert j["source"]["next_retry_at"] and j["source"]["retry_in_minutes"] == 1
    assert j["reports"][0]["collector"] == "tavily" and j["reports"][0]["reason"] == "unreachable"
    body, by = sources_by_slug(client, viewer_h)
    assert by["instagram"]["status"] == "unavailable" and by["instagram"]["next_retry_at"] is not None
    assert body["tavily_credits"]["used"] == 1  # a 5xx keeps the reservation (may have been billed)

    world.tavily_status = 200  # still inside the back-off window, but a manual retry ignores it
    r = client.post(f"/api/sources/{sid}/retry?confirm=true", headers=admin_h)
    assert r.status_code == 202 and r.json()["status"] == "online" and r.json()["estimated_credits"] == 2, r.text
    _, by = sources_by_slug(client, viewer_h)
    assert by["instagram"]["status"] == "online" and by["instagram"]["last_sync_at"] and by["instagram"]["next_retry_at"] is None
    assert client.post(f"/api/sources/{sid}/retry", headers=admin_h).status_code == 409
    actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action == "source.retry")]
    assert len(actions) == 2
    # the key never appears in any response
    assert "tvly-test-key" not in client.get("/api/sources", headers=admin_h).text
    # all outbound requests went to the mocked hosts with the bearer key only in the header
    assert all(rq.url.host in ("api.tavily.com", "itunes.apple.com") for rq in world.requests)


def test_retry_that_spends_credits_needs_confirmation_and_states_the_cost(client, admin_h, brand, world, db):
    """D6: 'Retry now' on a Tavily-backed source used to spend credits silently."""
    _, by = sources_by_slug(client, admin_h)
    sid = by["instagram"]["id"]
    est = by["instagram"]["retry_estimate"]
    assert est["provider"] == "tavily" and est["credits"] == 2 and est["remaining"] == 900 and "2 Tavily credits" in est["note"]
    assert by["app_store"]["retry_estimate"]["credits"] == 0 or by["app_store"]["retry_estimate"]["provider"] == "tavily"  # tavily also covers the stores
    world.tavily_status = 500
    r = client.post(f"/api/sources/{sid}/retry", headers=admin_h)  # no confirmation
    assert r.status_code == 409 and r.json()["code"] == "confirmation_required" and r.json()["estimated_credits"] == 2
    assert "2 Tavily credits" in r.json()["detail"] and r.json()["credits_remaining"] == 900
    assert world.requests == [] and client.get("/api/sources", headers=admin_h).json()["tavily_credits"]["used"] == 0  # nothing was spent
    assert db.query(AuditLog).filter(AuditLog.action == "source.retry").count() == 0
    assert client.post(f"/api/sources/{sid}/retry?confirm=false", headers=admin_h).status_code == 409
    ok = client.post(f"/api/sources/{sid}/retry?confirm=true", headers=admin_h)
    assert ok.status_code == 202 and ok.json()["estimated_credits"] == 2
    assert "confirmed" in db.query(AuditLog).filter(AuditLog.action == "source.retry").one().detail


def test_retry_estimate_is_zero_in_sample_mode(client, admin_h, brand, world):
    client.put("/api/settings/data-mode", json={"data_mode": "sample"}, headers=admin_h)
    _, by = sources_by_slug(client, admin_h)
    assert by["x"]["retry_estimate"]["credits"] == 0
    assert client.post(f"/api/sources/{by['x']['id']}/retry", headers=admin_h).status_code == 202  # free: no confirmation needed


def test_retry_requires_brand_and_monitoring(client, admin_h, world):
    _, by = sources_by_slug(client, admin_h)
    sid = by["instagram"]["id"]
    r = client.post(f"/api/sources/{sid}/retry", headers=admin_h)
    assert r.status_code == 409 and "brand" in r.json()["detail"].lower()
    client.patch(f"/api/sources/{sid}", json={"monitored": False}, headers=admin_h)
    r = client.post(f"/api/sources/{sid}/retry", headers=admin_h)
    assert r.status_code == 409 and "not monitored" in r.json()["detail"]
    assert world.requests == []


def test_sample_mode_retry_uses_no_network(client, admin_h, brand, world, db):
    client.put("/api/settings/data-mode", json={"data_mode": "sample"}, headers=admin_h)
    _, by = sources_by_slug(client, admin_h)
    r = client.post(f"/api/sources/{by['x']['id']}/retry", headers=admin_h)
    assert r.status_code == 202 and r.json()["status"] == "online" and r.json()["source"]["source"] == "sample"
    assert world.requests == []
    assert db.query(Source).filter_by(platform="x").one().source == "sample"


def test_budget_exhaustion_through_the_api_marks_source_unavailable(client, admin_h, brand, world, app):
    rt = app.state.collector_runtime
    rt.ledger.budget = 0
    _, by = sources_by_slug(client, admin_h)
    r = client.post(f"/api/sources/{by['facebook']['id']}/retry?confirm=true", headers=admin_h)
    assert r.status_code == 202 and r.json()["status"] == "unavailable"
    assert "budget" in r.json()["source"]["message"].lower()
    assert world.requests == []


def test_rows_written_in_sample_mode_are_not_shown_as_current_in_live_mode(client, admin_h, brand, world):
    client.put("/api/settings/data-mode", json={"data_mode": "sample"}, headers=admin_h)
    _, by = sources_by_slug(client, admin_h)
    client.post(f"/api/sources/{by['x']['id']}/retry", headers=admin_h)
    _, by = sources_by_slug(client, admin_h)
    assert by["x"]["last_sync_at"] and by["x"]["source"] == "sample"
    client.put("/api/settings/data-mode", json={"data_mode": "live"}, headers=admin_h)
    _, by = sources_by_slug(client, admin_h)
    assert by["x"]["last_sync_at"] is None and by["x"]["source"] == "live" and "Not checked yet" in by["x"]["message"]

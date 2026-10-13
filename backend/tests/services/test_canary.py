"""The canary: a planted fake that every scan must find. Also proves it cannot touch real data or the network."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import func, select

from app.core.collectors.sample import SampleCollector
from app.core.pipeline import scan_pipeline as sp
from app.models import AuditLog, Brand, Detection, OfficialMatch
from app.services import canary
from tests.conftest import headers_for
from tests.scan_helpers import all_detections, make_client, run_scan, set_mode


def test_canary_passes_and_reports_every_check():
    r = canary.run_canary()
    assert r["passed"] is True and r["failures"] == []
    names = {c["name"] for c in r["checks"]}
    assert names == {"scan_completed", "lookalike_profile_detected", "lookalike_app_detected", "official_assets_excluded",
                     "unrelated_business_not_flagged", "score_is_explained", "results_labelled_sample"}
    assert all(c["passed"] for c in r["checks"])
    assert "does not prove live sources work" in r["scope"]


def test_full_sample_scan_finds_canary_lookalike_and_excludes_canary_official(settings, checker):
    """(a) A normal API scan (sample collector + the planted candidates) on a test brand named like the canary."""
    with make_client(settings, checker, [canary.CanaryCollector(), SampleCollector()]) as c:
        h = headers_for(c, "admin@example.com", "Admin-Pass-for-tests-1")
        set_mode(c, h, "sample")
        b = c.post("/api/brands", json={"name": canary.BRAND_NAME, "aliases": ["Zephyrine Vault"], "keywords": ["zephyrinevault"]}, headers=h).json()
        assert c.post(f"/api/brands/{b['id']}/accounts", json={"platform": "instagram", "handle": canary.OFFICIAL_HANDLE}, headers=h).status_code == 201
        assert c.post(f"/api/brands/{b['id']}/apps", json={"name": "Zephyrine Vault Bank Mobile", "package_id": canary.OFFICIAL_PACKAGE,
                                                           "publisher": "Zephyrine Vault Bank Ltd", "stores": ["google_play"]}, headers=h).status_code == 201
        scan = run_scan(c, h)
        assert scan["status"] == "completed"
        items = all_detections(c, h)
        fake = next(i for i in items if (i["handle"] or "").lstrip("@") == canary.FAKE_HANDLE)
        assert fake["score"] >= canary.FLAG_MIN_SCORE and fake["status"] == "new" and fake["source"] == "sample"
        assert any(i["package_id"] == canary.FAKE_PACKAGE for i in items)
        official = {canary.OFFICIAL_HANDLE, canary.OFFICIAL_PACKAGE}
        assert not [i for i in items if (i["handle"] or "").lstrip("@").lower() in official or i["package_id"] in official]
        excluded = c.get("/api/detections/excluded", headers=h).json()["items"]
        assert {canary.OFFICIAL_HANDLE, canary.OFFICIAL_PACKAGE} <= {w.lstrip("@") for i in excluded for w in i["label"].split()}
        clean = [i for i in items if (i["handle"] or "").lstrip("@") == canary.CLEAN_HANDLE]
        assert all(i["score"] < canary.FLAG_MIN_SCORE for i in clean)


def test_canary_fails_when_allowlist_regresses(monkeypatch):
    monkeypatch.setattr(sp.BrandAllowlist, "check", lambda self, c: None)  # allowlist stops excluding official assets
    r = canary.run_canary()
    assert r["passed"] is False
    assert any(f.startswith("official_assets_excluded") for f in r["failures"])


def test_canary_fails_when_detection_regresses(monkeypatch):
    monkeypatch.setattr(sp, "evaluate", lambda *a, **k: SimpleNamespace(kind="ignored"))  # nothing is ever flagged
    r = canary.run_canary()
    assert r["passed"] is False
    assert any(f.startswith("lookalike_profile_detected") for f in r["failures"])
    assert any(f.startswith("lookalike_app_detected") for f in r["failures"])


def test_canary_fails_when_scoring_regresses(monkeypatch):
    from app.core.engine import risk_engine

    real = risk_engine.score_social

    def flat(*a, **k):
        res = real(*a, **k)
        res.score = 5
        return res

    monkeypatch.setattr(risk_engine, "score_social", flat)
    r = canary.run_canary()
    assert r["passed"] is False and any(f.startswith("lookalike_profile_detected") for f in r["failures"])


def test_canary_crash_is_reported_not_raised(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(canary, "request_scan", boom)
    r = canary.run_canary()
    assert r["passed"] is False and r["failures"] == ["canary_ran: the canary itself crashed: RuntimeError"]


def test_canary_endpoint_admin_only_and_never_touches_real_data(client, db, admin_h, analyst_h):
    assert client.post("/api/health/canary").status_code == 401
    assert client.post("/api/health/canary", headers=analyst_h).status_code == 403
    r = client.post("/api/health/canary", headers=admin_h)
    assert r.status_code == 200 and r.json()["passed"] is True
    # the real database has no canary brand, detection or official match; only an audit row records the run
    assert db.scalar(select(func.count()).select_from(Brand)) == 0
    assert db.scalar(select(func.count()).select_from(Detection)) == 0
    assert db.scalar(select(func.count()).select_from(OfficialMatch)) == 0
    assert client.get("/api/detections", headers=admin_h).status_code in (200, 404, 409)
    row = db.scalars(select(AuditLog).where(AuditLog.action == "health.canary")).one()
    assert row.detail == "passed"


def test_canary_endpoint_reports_failure_with_200(client, admin_h, monkeypatch):
    monkeypatch.setattr(sp.BrandAllowlist, "check", lambda self, c: None)
    r = client.post("/api/health/canary", headers=admin_h)
    assert r.status_code == 200 and r.json()["passed"] is False and r.json()["failures"]


def test_canary_ignores_environment_keys(monkeypatch):
    """A developer's real keys in the shell must not reach the canary (and the network guard would fail the test if they did)."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-should-never-be-used")
    monkeypatch.setenv("REDIS_URL", "redis://nope:6379/0")
    assert canary.run_canary()["passed"] is True


def test_cli_exit_codes(capsys, monkeypatch):
    from scripts import canary as cli

    monkeypatch.setattr("sys.argv", ["canary"])
    assert cli.main() == 0
    assert "canary PASSED" in capsys.readouterr().out
    monkeypatch.setattr(sp.BrandAllowlist, "check", lambda self, c: None)
    assert cli.main() == 1
    assert "FAIL official_assets_excluded" in capsys.readouterr().out

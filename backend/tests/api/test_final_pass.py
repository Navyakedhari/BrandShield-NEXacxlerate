"""Resolution workflow (migration 0007), extended evaluation, general rate limit, CORS."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config.config import Settings
from app.db.base import Base
from app.db.session import make_engine
from app.main import create_app
from app.models import AuditLog


def _det(client, h, brand) -> int:
    r = client.post("/api/candidates/social", json={"platform": "instagram", "handle": "acme_bank_support", "display_name": "Acme Bank Support"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["detection_id"]


def test_resolution_set_persist_filter_audit(client, db, admin_h, viewer_h, brand):
    i = _det(client, admin_h, brand)
    assert client.get(f"/api/detections/{i}", headers=admin_h).json()["resolution"] is None
    assert client.post(f"/api/detections/{i}/resolution", json={"resolution": "monitoring"}, headers=viewer_h).status_code == 403
    assert client.post(f"/api/detections/{i}/resolution", json={"resolution": "bogus"}, headers=admin_h).status_code == 422
    r = client.post(f"/api/detections/{i}/resolution", json={"resolution": "false_positive", "note": "fan page"}, headers=admin_h)
    assert r.status_code == 200 and r.json()["resolution"] == "false_positive" and r.json()["audit_id"]
    d = client.get(f"/api/detections/{i}", headers=admin_h).json()
    assert d["resolution"] == "false_positive" and d["status"] == "new"
    assert d["history"][-1]["kind"] == "resolution" and d["history"][-1]["note"] == "fan page"
    items = client.get("/api/detections?source=all&resolution=false_positive", headers=admin_h).json()["items"]
    assert [x["id"] for x in items] == [i]
    assert client.get("/api/detections?source=all&resolution=resolved", headers=admin_h).json()["items"] == []
    assert db.query(AuditLog).filter_by(action="detection.resolution_set").count() == 1


def test_evaluation_has_confusion_dataset_and_hard_negatives(client, admin_h, brand):
    s = client.get("/api/evaluation", headers=admin_h).json()["synthetic"]
    m = s["confusion"]["matrix"]
    assert m["tp"] + m["fp"] + m["fn"] + m["tn"] == s["dataset"]["size"] == s["confusion"]["total_cases"]
    assert 0 <= s["confusion"]["false_positive_rate"] <= 1
    assert s["dataset"]["synthetic"] is True and "SYNTHETIC" in s["dataset"]["banner"] and s["dataset"]["version"]
    groups = {g["group"] for g in s["confusion"]["negative_groups"]}
    assert {"fan_page", "authorized_reseller", "hard_negative", "unrelated"} <= groups
    assert s["algorithm_version"]["web"] == "2.0"
    by_cat = client.get("/api/evaluation", headers=admin_h).json()["by_category"]
    assert {c["category"] for c in by_cat} >= {"social", "apps", "web"}


def _limited(settings: Settings, limit: int):
    s = Settings(_env_file=None, data_dir=settings.data_dir, database_url="sqlite://", api_rate_limit_per_minute=limit)  # type: ignore[call-arg]
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return TestClient(create_app(s, engine=engine))


def test_rate_limit_429_but_health_exempt(settings):
    with _limited(settings, 5) as c:
        codes = [c.get("/api/auth/me").status_code for _ in range(8)]
        assert codes[:5] == [401] * 5 and codes[5:] == [429] * 3
        assert c.get("/health").status_code in (200, 503)
        assert c.get("/api/auth/me").headers.get("retry-after") == "60"


def test_rate_limit_off_with_zero(settings):
    with _limited(settings, 0) as c:
        assert all(c.get("/api/auth/me").status_code == 401 for _ in range(20))


def test_cors_is_restrictive(client):
    ok = client.options("/api/auth/me", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/auth/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
    assert "access-control-allow-credentials" not in ok.headers

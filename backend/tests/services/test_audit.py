import io

from PIL import Image
from sqlalchemy import select

from app.core.pipeline.url_safety import CheckResult
from app.models import AuditLog
from tests.conftest import ADMIN, login


def actions(db):
    db.expire_all()
    return [r.action for r in db.scalars(select(AuditLog).order_by(AuditLog.id))]


def png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (300, 300), (1, 2, 3)).save(b, format="PNG")
    return b.getvalue()


def test_every_mutating_action_writes_one_audit_row(client, db, admin_h):
    assert actions(db) == ["auth.login"]  # admin_h fixture logged in
    b = client.post("/api/brands", json={"name": "Acme"}, headers=admin_h).json()
    bid = b["id"]
    steps = [
        ("brand.created", None),
        ("brand.updated", lambda: client.put(f"/api/brands/{bid}", json={"name": "Acme 2", "aliases": ["A"]}, headers=admin_h)),
        ("logo.replaced", lambda: client.post(f"/api/brands/{bid}/logo", files={"file": ("l.png", png())}, headers=admin_h)),
        ("account.added", lambda: client.post(f"/api/brands/{bid}/accounts", json={"platform": "x", "handle": "acme"}, headers=admin_h)),
        ("app.added", lambda: client.post(f"/api/brands/{bid}/apps", json={"name": "A", "package_id": "com.a.b", "publisher": "P", "stores": ["google_play"]}, headers=admin_h)),
        ("account.removed", lambda: client.delete(f"/api/brands/{bid}/accounts/1", headers=admin_h)),
        ("app.removed", lambda: client.delete(f"/api/brands/{bid}/apps/1", headers=admin_h)),
    ]
    for expected, call in steps[1:]:
        before = len(actions(db))
        r = call()
        assert r.status_code in (200, 201), (expected, r.text)
        after = actions(db)
        assert len(after) == before + 1
        assert after[-1] == expected
    assert actions(db)[1] == "brand.created"


def test_failed_validation_writes_no_audit_row(client, db, admin_h, brand):
    before = len(actions(db))
    client.post(f"/api/brands/{brand['id']}/accounts", json={"platform": "x", "handle": "a"}, headers=admin_h)
    client.post(f"/api/brands/{brand['id']}/apps", json={"name": "A"}, headers=admin_h)
    assert len(actions(db)) == before


def test_duplicate_writes_no_audit_row(client, db, admin_h, brand):
    body = {"platform": "x", "handle": "acme"}
    client.post(f"/api/brands/{brand['id']}/accounts", json=body, headers=admin_h)
    before = len(actions(db))
    assert client.post(f"/api/brands/{brand['id']}/accounts", json=body, headers=admin_h).status_code == 409
    assert len(actions(db)) == before


def test_audit_row_content(client, db, admin_h, brand):
    db.expire_all()
    row = db.scalars(select(AuditLog).where(AuditLog.action == "brand.created")).one()
    assert row.actor == "Test Admin"
    assert row.actor_id is not None
    assert row.target == "Acme Bank"
    assert row.source == "live"
    assert row.ts is not None


def test_failed_check_is_recorded_in_audit_detail(client, db, admin_h, brand, checker):
    checker.result = CheckResult("unreachable", "timeout")
    client.post(f"/api/brands/{brand['id']}/accounts", json={"platform": "youtube", "handle": "acme"}, headers=admin_h)
    db.expire_all()
    row = db.scalars(select(AuditLog).where(AuditLog.action == "account.added")).one()
    assert row.detail == "unreachable"
    assert row.target == "@acme on youtube"


def test_login_failure_and_logout_are_audited(client, db):
    login(client, ADMIN[0], "wrong")
    tokens = login(client, *ADMIN).json()
    client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert actions(db) == ["auth.login_failed", "auth.login", "auth.logout"]


def test_audit_never_stores_password(client, db):
    login(client, ADMIN[0], "SuperSecretWrongPassword!")
    db.expire_all()
    for r in db.scalars(select(AuditLog)):
        assert "SuperSecretWrongPassword" not in f"{r.target}{r.detail}{r.actor}"


def test_get_audit_list_filter_and_pagination(client, admin_h, brand):
    r = client.get("/api/audit", headers=admin_h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # auth.login + brand.created
    assert body["items"][0]["action"] == "brand.created"  # newest first
    assert set(body["items"][0]) == {"id", "ts", "actor", "actor_id", "action", "target", "detail", "source"}
    assert body["items"][0]["ts"].endswith("Z")
    only = client.get("/api/audit", params={"action": "auth.login"}, headers=admin_h).json()
    assert only["total"] == 1
    page = client.get("/api/audit", params={"limit": 1, "offset": 1}, headers=admin_h).json()
    assert len(page["items"]) == 1 and page["total"] == 2
    assert client.get("/api/audit", params={"limit": 500}, headers=admin_h).status_code == 422


def test_audit_requires_auth_and_blocks_viewers(client, viewer_h, analyst_h):
    assert client.get("/api/audit").status_code == 401
    assert client.get("/api/audit", headers=viewer_h).status_code == 403
    assert client.get("/api/audit", headers=analyst_h).status_code == 200


def test_audit_export_csv_neutralises_formulas(client, admin_h):
    client.post("/api/brands", json={"name": "=HYPERLINK(\"http://x\")"}, headers=admin_h)
    r = client.get("/api/audit/export.csv", headers=admin_h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.splitlines()
    assert lines[0] == "ts,actor,action,target,detail"
    assert any("'=HYPERLINK" in line for line in lines)
    assert not any(",=HYPERLINK" in line for line in lines)

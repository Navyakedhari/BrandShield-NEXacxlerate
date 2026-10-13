from datetime import timedelta

import jwt
from sqlalchemy import select

from app.config.security import create_access_token, create_refresh_token, hash_password, verify_password
from app.config.time import utcnow
from app.models import RefreshToken, User
from tests.conftest import ADMIN, login, make_user


def test_login_success_shape(client):
    r = login(client, *ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["user"]["email"] == ADMIN[0]
    assert body["user"]["role"] == "admin"
    assert body["user"]["default_brand_id"] is None
    assert "password" not in str(body)


def test_login_email_is_case_insensitive(client):
    assert login(client, "ADMIN@Example.com", ADMIN[1]).status_code == 200


def test_login_wrong_password(client):
    r = login(client, ADMIN[0], "wrong-password")
    assert r.status_code == 401
    assert r.json() == {"detail": "Incorrect email or password"}


def test_login_unknown_user_same_message(client):
    r = login(client, "nobody@example.com", "whatever")
    assert r.status_code == 401
    assert r.json() == {"detail": "Incorrect email or password"}


def test_login_inactive_user_rejected(client, db):
    make_user(db, "off@example.com", "viewer", active=False)
    assert login(client, "off@example.com", "Some-Password-1").status_code == 401


def test_login_rate_limited_after_repeated_failures(client):
    for _ in range(5):
        assert login(client, ADMIN[0], "bad").status_code == 401
    r = login(client, ADMIN[0], ADMIN[1])  # even the right password is blocked now
    assert r.status_code == 429
    assert "detail" in r.json()


def test_me_requires_token(client):
    r = client.get("/api/me")
    assert r.status_code == 401
    assert r.json() == {"detail": "Not authenticated"}
    assert client.get("/api/auth/me").status_code == 401


def test_me_with_token(client, admin_h):
    for path in ("/api/me", "/api/auth/me"):
        r = client.get(path, headers=admin_h)
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN[0]


def test_default_brand_id_reflects_brand(client, admin_h, brand):
    assert client.get("/api/me", headers=admin_h).json()["default_brand_id"] == brand["id"]


def test_garbage_token_rejected(client):
    r = client.get("/api/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or expired token"}


def test_expired_token_rejected(client, settings, db):
    uid = db.scalar(select(User.id))
    expired = jwt.encode(
        {"sub": str(uid), "typ": "access", "jti": "x", "exp": int((utcnow() - timedelta(minutes=1)).timestamp())},
        settings.jwt_secret,
        algorithm="HS256",
    )
    r = client.get("/api/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or expired token"}


def test_token_signed_with_other_secret_rejected(client, db):
    uid = db.scalar(select(User.id))
    forged = jwt.encode(
        {"sub": str(uid), "typ": "access", "jti": "x", "exp": int((utcnow() + timedelta(hours=1)).timestamp())},
        "a-different-secret-entirely-0000000",
        algorithm="HS256",
    )
    assert client.get("/api/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client, settings, db):
    uid = db.scalar(select(User.id))
    refresh, _ = create_refresh_token(settings, uid)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {refresh}"}).status_code == 401


def test_deactivated_user_token_rejected(client, settings, db):
    u = make_user(db, "gone@example.com", "viewer")
    token = create_access_token(settings, u.id)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    u.active = False
    db.commit()
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_refresh_rotates_and_old_token_is_revoked(client):
    first = login(client, *ADMIN).json()
    r = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {second['access_token']}"}).status_code == 200
    # Re-using the rotated-out token fails and burns the whole family.
    assert client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401


def test_refresh_with_access_token_or_garbage_rejected(client):
    tokens = login(client, *ADMIN).json()
    assert client.post("/api/auth/refresh", json={"refresh_token": tokens["access_token"]}).status_code == 401
    r = client.post("/api/auth/refresh", json={"refresh_token": "garbage-garbage-garbage"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or expired refresh token"}


def test_logout_revokes_refresh_token(client, db):
    tokens = login(client, *ADMIN).json()
    r = client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "detail": ""}
    assert client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert all(t.revoked_at for t in db.scalars(select(RefreshToken)))


def test_admin_seeded_once_and_password_not_overwritten(app, client, db, settings):
    from app.services.seed import seed_admin

    assert seed_admin(db, settings) is False
    assert db.query(User).count() == 1


def test_password_hashing_roundtrip():
    h = hash_password("correct horse")
    assert h != "correct horse"
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_viewer_cannot_mutate(client, viewer_h):
    r = client.post("/api/brands", json={"name": "Nope"}, headers=viewer_h)
    assert r.status_code == 403
    assert r.json() == {"detail": "You do not have permission to do this"}


def test_analyst_can_mutate(client, analyst_h):
    assert client.post("/api/brands", json={"name": "Yes"}, headers=analyst_h).status_code == 201

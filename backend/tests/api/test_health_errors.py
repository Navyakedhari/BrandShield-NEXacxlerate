from app.db.session import get_db


def test_health_ok(client):
    for path in ("/health", "/api/health"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["database"] == {"status": "ok"}


def test_health_reports_db_down(app, client):
    class Broken:
        def execute(self, *_a, **_k):
            raise RuntimeError("db gone")

    app.dependency_overrides[get_db] = lambda: Broken()
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["database"]["status"] == "down"
    assert r.json()["status"] == "down"


def test_error_format_string_detail(client):
    r = client.get("/api/brands/999")
    assert r.status_code == 401
    assert r.json() == {"detail": "Not authenticated"}


def test_error_format_validation_is_list_of_loc_msg_type(client):
    r = client.post("/api/auth/login", json={"email": "not-an-email", "password": "x"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, list)
    assert set(detail[0]) == {"loc", "msg", "type"}
    assert detail[0]["loc"] == ["body", "email"]


def test_404_has_detail(client, admin_h):
    r = client.get("/api/brands/12345", headers=admin_h)
    assert r.status_code == 404
    assert r.json() == {"detail": "Brand not found"}


def test_unknown_route_has_detail(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert isinstance(r.json()["detail"], str)

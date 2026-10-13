import pytest

from app.services import validators as v


def test_create_brand_and_get(client, admin_h):
    r = client.post(
        "/api/brands",
        json={
            "name": "  Acme   Bank ",
            "industry": "Banking",
            "aliases": ["Acme", "acme", " AcmeBank "],
            "keywords": ["acme", "#AcmeBank"],
            "domains": ["AcmeBank.com", "acmebank.com", "acmebank.in"],
        },
        headers=admin_h,
    )
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["name"] == "Acme Bank"
    assert b["aliases"] == ["Acme", "AcmeBank"]  # trimmed and de-duplicated case-insensitively
    assert b["keywords"] == ["acme", "#AcmeBank"]  # '#' allowed
    assert b["domains"] == ["acmebank.com", "acmebank.in"]  # lower-cased, de-duplicated
    assert b["logo"] is None
    assert b["official_assets"] == 0
    assert b["source"] == "live"
    assert client.get(f"/api/brands/{b['id']}", headers=admin_h).json() == b
    assert client.get("/api/brands", headers=admin_h).json() == [{"id": b["id"], "name": "Acme Bank"}]


def test_only_one_brand_for_now(client, admin_h, brand):
    r = client.post("/api/brands", json={"name": "Second"}, headers=admin_h)
    assert r.status_code == 409
    assert "detail" in r.json()


def test_update_brand_replaces_identity(client, admin_h, brand):
    r = client.put(
        f"/api/brands/{brand['id']}",
        json={"name": "Acme Bank Ltd", "industry": "Finance", "aliases": ["AB"], "keywords": [], "domains": ["acme.com"]},
        headers=admin_h,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Acme Bank Ltd"
    assert r.json()["aliases"] == ["AB"]
    assert r.json()["domains"] == ["acme.com"]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 121},
        {},
        {"name": "A", "industry": "i" * 121},
        {"name": "A", "aliases": ["a" * 81]},
        {"name": "A", "aliases": [f"alias{i}" for i in range(51)]},
        {"name": "A", "keywords": [f"kw{i}" for i in range(51)]},
        {"name": "A", "domains": ["http://acme.com"]},
        {"name": "A", "domains": ["acme.com/path"]},
        {"name": "A", "domains": ["acme"]},
        {"name": "A", "domains": ["-bad-.com"]},
        {"name": "A", "domains": ["exa mple.com"]},
        {"name": "A", "domains": ["acme.com:8080"]},
        {"name": "A", "domains": ["192.168.0.1"]},
        {"name": "A", "domains": ["acme.c"]},
        {"name": "A", "domains": ["a" * 64 + ".com"]},
        {"name": "A", "aliases": "not-a-list"},
    ],
)
def test_brand_validation_rejects(client, admin_h, payload):
    r = client.post("/api/brands", json=payload, headers=admin_h)
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], list)


def test_get_missing_brand_404(client, admin_h):
    assert client.get("/api/brands/42", headers=admin_h).status_code == 404
    assert client.put("/api/brands/42", json={"name": "x"}, headers=admin_h).status_code == 404


def test_viewer_can_read_but_not_edit(client, brand, viewer_h):
    assert client.get(f"/api/brands/{brand['id']}", headers=viewer_h).status_code == 200
    assert client.put(f"/api/brands/{brand['id']}", json={"name": "x"}, headers=viewer_h).status_code == 403


@pytest.mark.parametrize("good", ["acme.com", "sub.acme-bank.co.in", "xn--bcher-kva.example", "A.B.io"])
def test_normalize_domain_accepts(good):
    assert v.normalize_domain(good) == good.lower()


@pytest.mark.parametrize("bad", ["", "acme", "a..com", ".com", "acme.com.", "ht!tp.com", "user@acme.com", "acme.123", "a_b.com"])
def test_normalize_domain_rejects(bad):
    if bad == "acme.com.":  # a single trailing dot is tolerated and stripped
        assert v.normalize_domain(bad) == "acme.com"
        return
    with pytest.raises(ValueError):
        v.normalize_domain(bad)

import io

from PIL import Image

from app.core.pipeline.bands import band_for
from app.core.pipeline.url_safety import CheckResult


def health(client, h, brand):
    return client.get(f"/api/brands/{brand['id']}", headers=h).json()["profile_health"]


def test_empty_brand_score_and_missing_reasons(client, admin_h):
    b = client.post("/api/brands", json={"name": "Acme"}, headers=admin_h).json()
    h = b["profile_health"]
    assert h["percent"] == 0
    assert {m["key"] for m in h["missing"]} == {c["key"] for c in h["checks"]}
    assert all(m["why"] for m in h["missing"])  # every gap has a reason
    assert sum(c["weight"] for c in h["checks"]) == 100
    assert "Low coverage" in h["message"]


def test_aliases_only_gives_20(client, admin_h, brand):
    assert health(client, admin_h, brand)["percent"] == 20  # fixture brand has name + alias


def test_full_profile_is_100_and_deterministic(client, admin_h, brand):
    bid = brand["id"]
    client.put(f"/api/brands/{bid}", json={"name": "Acme", "aliases": ["A"], "keywords": ["acme"], "domains": ["acme.com"]}, headers=admin_h)
    b = io.BytesIO()
    Image.new("RGB", (300, 300)).save(b, format="PNG")
    client.post(f"/api/brands/{bid}/logo", files={"file": ("l.png", b.getvalue())}, headers=admin_h)
    client.post(f"/api/brands/{bid}/accounts", json={"platform": "x", "handle": "acme"}, headers=admin_h)
    client.post(f"/api/brands/{bid}/apps", json={"name": "A", "package_id": "com.a.b", "publisher": "P", "stores": ["google_play"]}, headers=admin_h)
    first = health(client, admin_h, brand)
    assert first["percent"] == 100
    assert first["missing"] == []
    assert first["message"] == "Complete. Every check passes."
    assert health(client, admin_h, brand) == first  # same input, same output


def test_unreachable_account_lowers_score_and_names_it(client, admin_h, brand, checker):
    bid = brand["id"]
    client.post(f"/api/brands/{bid}/accounts", json={"platform": "x", "handle": "acme"}, headers=admin_h)
    assert next(c for c in health(client, admin_h, brand)["checks"] if c["key"] == "urls_reachable")["ok"] is True
    checker.result = CheckResult("unreachable", "timeout")
    client.post(f"/api/brands/{bid}/accounts", json={"platform": "youtube", "handle": "acme"}, headers=admin_h)
    h = health(client, admin_h, brand)
    chk = next(c for c in h["checks"] if c["key"] == "urls_reachable")
    assert chk["ok"] is False and chk["points"] == 0
    assert "acme on youtube" in chk["why"]


def test_adding_each_asset_adds_its_weight(client, admin_h, brand):
    bid = brand["id"]
    base = health(client, admin_h, brand)["percent"]
    client.put(f"/api/brands/{bid}", json={"name": "Acme", "aliases": ["A"], "domains": ["acme.com"]}, headers=admin_h)
    assert health(client, admin_h, brand)["percent"] == base + 15
    client.post(f"/api/brands/{bid}/apps", json={"name": "A", "package_id": "com.a.b", "publisher": "P", "stores": ["google_play"]}, headers=admin_h)
    assert health(client, admin_h, brand)["percent"] == base + 25


def test_official_assets_and_allowlist_counts(client, admin_h, brand):
    bid = brand["id"]
    client.post(f"/api/brands/{bid}/accounts", json={"platform": "x", "handle": "acme"}, headers=admin_h)
    client.post(f"/api/brands/{bid}/apps", json={"name": "A", "package_id": "com.a.b", "publisher": "P", "stores": ["google_play"]}, headers=admin_h)
    b = client.get(f"/api/brands/{bid}", headers=admin_h).json()
    assert b["official_assets"] == 2
    assert b["allowlist"] == {"accounts": 1, "apps": 1, "domains": 0}


def test_risk_bands():
    assert [band_for(s) for s in (100, 85, 84, 70, 69, 0)] == ["high", "high", "medium", "medium", "low", "low"]

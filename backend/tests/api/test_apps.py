import pytest

GOOD = {"name": "Acme Mobile", "package_id": "com.acmebank.mobile", "publisher": "Acme Bank Ltd", "stores": ["google_play"]}


def add(client, h, brand, **over):
    return client.post(f"/api/brands/{brand['id']}/apps", json={**GOOD, **over}, headers=h)


def test_add_app(client, admin_h, brand):
    r = add(client, admin_h, brand)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["package_id"] == "com.acmebank.mobile"
    assert a["stores"] == ["google_play"]
    assert a["status"] == "verified"
    assert a["source"] == "live"


def test_stores_deduplicated_and_lowercased(client, admin_h, brand):
    r = add(client, admin_h, brand, stores=["Google_Play", "google_play", "app_store"])
    assert r.status_code == 201
    assert r.json()["stores"] == ["google_play", "app_store"]


@pytest.mark.parametrize(
    "over",
    [
        {"name": ""},
        {"name": "n" * 121},
        {"publisher": ""},
        {"publisher": "p" * 121},
        {"package_id": ""},
        {"stores": []},
        {"stores": ["amazon"]},
        {"stores": ["google_play", "windows"]},
        {"package_id": "x" * 156},
    ],
)
def test_app_required_fields_and_limits(client, admin_h, brand, over):
    assert add(client, admin_h, brand, **over).status_code == 422


@pytest.mark.parametrize(
    "pkg",
    ["acmebank", "com", "com..bank", "1com.bank", "com.1bank", "com.bank-app", "com.bank app", ".com.bank", "com.bank."],
)
def test_google_play_package_invalid(client, admin_h, brand, pkg):
    assert add(client, admin_h, brand, package_id=pkg, stores=["google_play"]).status_code == 422


@pytest.mark.parametrize("pkg", ["com.acme.app", "com.Acme_Bank.app2", "in.co.acme.upi"])
def test_google_play_package_valid(client, admin_h, brand, pkg):
    assert add(client, admin_h, brand, package_id=pkg, stores=["google_play"]).status_code == 201


@pytest.mark.parametrize("bundle", ["acme", "com.acme_bank", "com..acme", "com.acme app", "com.acme!"])
def test_app_store_bundle_invalid(client, admin_h, brand, bundle):
    assert add(client, admin_h, brand, package_id=bundle, stores=["app_store"]).status_code == 422


@pytest.mark.parametrize("bundle", ["com.acme.app", "com.acme-bank.app", "com.1acme.app"])
def test_app_store_bundle_valid(client, admin_h, brand, bundle):
    assert add(client, admin_h, brand, package_id=bundle, stores=["app_store"]).status_code == 201


def test_underscore_ok_on_play_but_not_when_also_on_app_store(client, admin_h, brand):
    assert add(client, admin_h, brand, package_id="com.acme_bank.app", stores=["google_play"]).status_code == 201
    r = add(client, admin_h, brand, package_id="com.acme_bank.other", stores=["google_play", "app_store"])
    assert r.status_code == 422


def test_duplicate_package_rejected_case_insensitive(client, admin_h, brand):
    assert add(client, admin_h, brand).status_code == 201
    r = add(client, admin_h, brand, package_id="COM.AcmeBank.Mobile", name="Other")
    assert r.status_code == 409
    assert "detail" in r.json()


def test_list_and_delete_app(client, admin_h, brand):
    a = add(client, admin_h, brand).json()
    assert client.get(f"/api/brands/{brand['id']}/apps", headers=admin_h).json()["total"] == 1
    assert client.delete(f"/api/brands/{brand['id']}/apps/{a['id']}", headers=admin_h).status_code == 200
    assert client.delete(f"/api/brands/{brand['id']}/apps/{a['id']}", headers=admin_h).status_code == 404
    assert client.get(f"/api/brands/{brand['id']}/apps", headers=admin_h).json()["total"] == 0


def test_app_requires_writer(client, brand, viewer_h):
    assert client.post(f"/api/brands/{brand['id']}/apps", json=GOOD).status_code == 401
    assert client.post(f"/api/brands/{brand['id']}/apps", json=GOOD, headers=viewer_h).status_code == 403

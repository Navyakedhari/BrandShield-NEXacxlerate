import pytest

from app.core.pipeline.url_safety import CheckResult
from app.services import validators as v


def add(client, h, brand, platform="instagram", handle="acmebank"):
    return client.post(f"/api/brands/{brand['id']}/accounts", json={"platform": platform, "handle": handle}, headers=h)


def test_add_account_normalizes_and_builds_url(client, admin_h, brand, checker):
    r = add(client, admin_h, brand, "instagram", "@AcmeBank")
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["handle"] == "@AcmeBank"  # display form for instagram
    assert a["url"] == "instagram.com/AcmeBank"
    assert a["status"] == "verified"
    assert a["warning"] is None
    assert a["source"] == "live"
    assert checker.calls == ["instagram.com/AcmeBank"]


@pytest.mark.parametrize(
    "platform,handle,url,display",
    [
        ("x", "AcmeBank", "x.com/AcmeBank", "@AcmeBank"),
        ("facebook", "acme.bank", "facebook.com/acme.bank", "acme.bank"),
        ("linkedin", "acme_bank", "linkedin.com/company/acme_bank", "acme_bank"),
        ("youtube", "acmebank", "youtube.com/@acmebank", "@acmebank"),
    ],
)
def test_platform_url_and_display(client, admin_h, brand, platform, handle, url, display):
    a = add(client, admin_h, brand, platform, handle).json()
    assert a["url"] == url
    assert a["handle"] == display


@pytest.mark.parametrize(
    "handle",
    ["ab", "a" * 31, "has space", "bad-dash", "bad!", "", "@@acme", "@", "ünïcode", "acme/bank", "a b c"],
)
def test_invalid_handle_rejected(client, admin_h, brand, handle):
    r = add(client, admin_h, brand, "instagram", handle)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("handle", ["abc", "a" * 30, "a.b_c9", "@abc"])
def test_valid_handle_boundaries(client, admin_h, brand, handle):
    assert add(client, admin_h, brand, "x", handle).status_code == 201


def test_unknown_platform_rejected(client, admin_h, brand):
    assert add(client, admin_h, brand, "tiktok", "acmebank").status_code == 422
    assert add(client, admin_h, brand, "telegram", "acmebank").status_code == 422  # detections only


def test_duplicate_same_platform_case_insensitive_rejected(client, admin_h, brand):
    assert add(client, admin_h, brand, "instagram", "AcmeBank").status_code == 201
    for variant in ("acmebank", "@ACMEBANK", " AcmeBank "):
        r = add(client, admin_h, brand, "instagram", variant)
        assert r.status_code == 409, variant
        assert "detail" in r.json()


def test_same_handle_other_platform_allowed(client, admin_h, brand):
    assert add(client, admin_h, brand, "instagram", "acmebank").status_code == 201
    assert add(client, admin_h, brand, "x", "acmebank").status_code == 201


def test_failed_check_is_a_warning_not_a_crash(client, admin_h, brand, checker):
    checker.result = CheckResult("unreachable", "The URL timed out")
    r = add(client, admin_h, brand, "youtube", "acmebank")
    assert r.status_code == 201
    assert r.json()["status"] == "unreachable"
    assert r.json()["warning"] == "The URL timed out"


def test_checker_raising_does_not_break_real_checker_contract():
    # The real checker converts every failure into a result; verify via an exploding resolver.
    from app.core.pipeline.url_safety import UrlChecker

    def boom(_host):
        raise RuntimeError("dns exploded")

    res = UrlChecker(resolver=boom).check("instagram.com/acme")
    assert res.status == "unreachable"


def test_list_and_delete_account(client, admin_h, brand):
    a = add(client, admin_h, brand).json()
    lst = client.get(f"/api/brands/{brand['id']}/accounts", headers=admin_h).json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == a["id"]
    assert client.delete(f"/api/brands/{brand['id']}/accounts/{a['id']}", headers=admin_h).json() == {"ok": True, "detail": ""}
    assert client.get(f"/api/brands/{brand['id']}/accounts", headers=admin_h).json()["total"] == 0
    assert client.delete(f"/api/brands/{brand['id']}/accounts/{a['id']}", headers=admin_h).status_code == 404
    assert add(client, admin_h, brand).status_code == 201  # can re-add after removal


def test_account_on_missing_brand_404(client, admin_h):
    r = client.post("/api/brands/99/accounts", json={"platform": "x", "handle": "abc"}, headers=admin_h)
    assert r.status_code == 404


def test_account_requires_auth_and_role(client, brand, viewer_h):
    body = {"platform": "x", "handle": "abcd"}
    assert client.post(f"/api/brands/{brand['id']}/accounts", json=body).status_code == 401
    assert client.post(f"/api/brands/{brand['id']}/accounts", json=body, headers=viewer_h).status_code == 403
    assert client.get(f"/api/brands/{brand['id']}/accounts", headers=viewer_h).status_code == 200


def test_normalize_handle_unit():
    assert v.normalize_handle("@abc") == "abc"
    assert v.normalize_handle("  abc  ") == "abc"
    with pytest.raises(ValueError):
        v.normalize_handle("@@abc")


@pytest.mark.parametrize(
    "platform,handle,url",
    [
        ("linkedin", "acme-bank", "linkedin.com/company/acme-bank"),
        ("linkedin", "acme-bank-india-private-limited", "linkedin.com/company/acme-bank-india-private-limited"),
        ("youtube", "acme-bank", "youtube.com/@acme-bank"),
    ],
)
def test_hyphen_allowed_for_linkedin_and_youtube(client, admin_h, brand, platform, handle, url):
    r = add(client, admin_h, brand, platform, handle)
    assert r.status_code == 201, r.text
    assert r.json()["url"] == url


@pytest.mark.parametrize("platform", ["instagram", "x", "facebook"])
def test_hyphen_still_rejected_elsewhere(client, admin_h, brand, platform):
    assert add(client, admin_h, brand, platform, "acme-bank").status_code == 422

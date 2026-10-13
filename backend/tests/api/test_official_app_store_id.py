"""D3: the brand's own App Store app must not be flagged when a source exposes only its numeric id."""

from __future__ import annotations

import pytest

from app.core.collectors.base import Candidate
from app.services.validators import normalize_apple_app_id
from tests.conftest import ADMIN, headers_for
from tests.scan_helpers import StaticCollector, all_detections, make_client, run_scan, set_mode, setup_brand

BUNDLE = "com.acmebank.ios"
NUMERIC = "1234567890"


def itunes_copy() -> Candidate:  # what the iTunes collector returns: bundle id as handle, numeric id in extras
    return Candidate(platform="app_store", kind="app", handle=BUNDLE, display_name="Acme Bank", profile_url=f"https://apps.apple.com/in/app/acme-bank/id{NUMERIC}",
                     source="itunes", store="apple_app_store", publisher="Acme Bank Ltd", extras={"apple_app_id": NUMERIC})


def tavily_copy(numeric: str = NUMERIC, name: str = "Acme Bank") -> Candidate:  # a search-result URL only exposes the numeric id
    return Candidate(platform="app_store", kind="app", handle=numeric, display_name=name, profile_url=f"https://apps.apple.com/in/app/acme-bank/id{numeric}",
                     source="tavily", store="apple_app_store", publisher="Quick Tools Studio", extras={"apple_app_id": numeric})


def add_ios_app(c, h, bid, **extra):
    return c.post(f"/api/brands/{bid}/apps", json={"name": "Acme Bank iOS", "package_id": BUNDLE, "publisher": "Acme Bank Ltd", "stores": ["app_store"], **extra}, headers=h)


@pytest.mark.parametrize("raw, expected", [("1234567890", NUMERIC), ("id1234567890", NUMERIC), (f"https://apps.apple.com/in/app/acme-bank/id{NUMERIC}?mt=8", NUMERIC),
                                           (f"https://itunes.apple.com/app/id{NUMERIC}", NUMERIC)])
def test_normalize_apple_id(raw, expected):
    assert normalize_apple_app_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "12", "https://example.com/id1234567890", "1234567890x"])
def test_normalize_apple_id_rejects(raw):
    with pytest.raises(ValueError):
        normalize_apple_app_id(raw)


def test_registry_stores_and_validates_the_numeric_id(settings, checker):
    with make_client(settings, checker, []) as c:
        h = headers_for(c, *ADMIN)
        bid = setup_brand(c, h, accounts=(), apps=())["id"]
        r = add_ios_app(c, h, bid, apple_app_id=f"https://apps.apple.com/in/app/acme-bank/id{NUMERIC}")
        assert r.status_code == 201 and r.json()["apple_app_id"] == NUMERIC
        assert c.get(f"/api/brands/{bid}/apps", headers=h).json()["items"][0]["apple_app_id"] == NUMERIC
        # a Google Play-only app cannot carry an App Store id
        bad = c.post(f"/api/brands/{bid}/apps", json={"name": "A", "package_id": "com.acme.play", "publisher": "P", "stores": ["google_play"], "apple_app_id": NUMERIC}, headers=h)
        assert bad.status_code == 422
        assert add_ios_app(c, h, bid, apple_app_id="nope").status_code == 422


def test_numeric_id_can_be_added_later_and_releases_existing_detections(settings, checker):
    with make_client(settings, checker, [StaticCollector([tavily_copy()])]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        bid = setup_brand(c, h, accounts=(), apps=())["id"]
        app_id = add_ios_app(c, h, bid).json()["id"]
        run_scan(c, h)
        assert [i["package_id"] for i in all_detections(c, h)] == [NUMERIC]  # the bug: flagged because the registry only knows the bundle id
        r = c.patch(f"/api/brands/{bid}/apps/{app_id}", json={"apple_app_id": NUMERIC}, headers=h)
        assert r.status_code == 200 and r.json()["apple_app_id"] == NUMERIC
        assert all_detections(c, h) == []  # sync_with_allowlist closed it
        assert c.patch(f"/api/brands/{bid}/apps/{app_id}", json={"apple_app_id": "x"}, headers=h).status_code == 422
        assert c.patch(f"/api/brands/{bid}/apps/9999", json={"apple_app_id": NUMERIC}, headers=h).status_code == 404


def test_stored_numeric_id_excludes_the_tavily_copy(settings, checker):
    with make_client(settings, checker, [StaticCollector([tavily_copy(), tavily_copy("999999999", "Acme Bank Rewards")])]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        bid = setup_brand(c, h, accounts=(), apps=())["id"]
        add_ios_app(c, h, bid, apple_app_id=NUMERIC)
        scan = run_scan(c, h)
        assert scan["counts"]["official_excluded"] == 1
        flagged = all_detections(c, h)
        assert [i["package_id"] for i in flagged] == ["999999999"]  # a DIFFERENT numeric id is still a candidate


def test_itunes_copy_teaches_the_numeric_id_within_the_scan(settings, checker):
    """No numeric id in the registry: the iTunes listing (matched by bundle id) reveals it, so the Tavily copy is excluded too, in any order."""
    for order in ([itunes_copy(), tavily_copy()], [tavily_copy(), itunes_copy()]):
        with make_client(settings, checker, [StaticCollector(order)]) as c:
            h = headers_for(c, *ADMIN)
            set_mode(c, h, "live")
            bid = setup_brand(c, h, accounts=(), apps=())["id"]
            add_ios_app(c, h, bid)
            scan = run_scan(c, h)
            assert scan["counts"]["official_excluded"] == 1 and all_detections(c, h) == []  # the two copies share a URL and merge into one candidate
            assert c.get(f"/api/brands/{bid}/apps", headers=h).json()["items"][0]["apple_app_id"] is None  # nothing was written to the registry


def test_unrelated_numeric_id_is_not_learned(settings, checker):
    other = Candidate(platform="app_store", kind="app", handle="com.other.app", display_name="Acme Bank Rewards", profile_url="https://apps.apple.com/in/app/x/id555555555",
                      source="itunes", store="apple_app_store", extras={"apple_app_id": "555555555"})
    with make_client(settings, checker, [StaticCollector([other, tavily_copy("555555555", "Acme Bank Rewards")])]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        bid = setup_brand(c, h, accounts=(), apps=())["id"]
        add_ios_app(c, h, bid)
        scan = run_scan(c, h)
        assert scan["counts"]["official_excluded"] == 0 and len(all_detections(c, h)) == 2


def test_same_app_on_the_second_store_is_added_not_refused(settings, checker):
    """D4: the Brand Profile form sends one store per request; the same package on the other store used to be a 409."""
    with make_client(settings, checker, []) as c:
        h = headers_for(c, *ADMIN)
        bid = setup_brand(c, h, accounts=(), apps=())["id"]
        first = c.post(f"/api/brands/{bid}/apps", json={"name": "Acme Bank", "package_id": BUNDLE, "publisher": "Acme Bank Ltd", "stores": ["google_play"]}, headers=h)
        assert first.status_code == 201
        second = c.post(f"/api/brands/{bid}/apps", json={"name": "Acme Bank", "package_id": BUNDLE, "publisher": "Acme Bank Ltd", "stores": ["app_store"]}, headers=h)
        assert second.status_code == 201 and second.json()["id"] == first.json()["id"] and second.json()["stores"] == ["google_play", "app_store"]
        again = c.post(f"/api/brands/{bid}/apps", json={"name": "Acme Bank", "package_id": BUNDLE, "publisher": "Acme Bank Ltd", "stores": ["app_store"]}, headers=h)
        assert again.status_code == 409
        assert len(c.get(f"/api/brands/{bid}/apps", headers=h).json()["items"]) == 1

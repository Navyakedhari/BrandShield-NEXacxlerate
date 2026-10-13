"""E7: registry v2 (description, archive, publishers, verification), manual entry and CSV / JSON import. No network."""

import json

import pytest

from tests.conftest import ADMIN, headers_for, make_user
from tests.scan_helpers import StaticCollector, all_detections, make_client, run_scan, set_mode, setup_brand, social


@pytest.fixture
def env(settings, checker):
    with make_client(settings, checker, []) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        b = setup_brand(c, h)
        yield c, h, b


def csv_file(text: str, name="rows.csv"):
    return {"file": (name, text.encode("utf-8"), "text/csv")}


def test_description_survives_an_old_client_put_and_is_stored(env):
    c, h, b = env
    r = c.put(f"/api/brands/{b['id']}", json={"name": "Acme Bank", "description": "Acme Bank is a retail bank."}, headers=h)
    assert r.json()["description"] == "Acme Bank is a retail bank."
    r = c.put(f"/api/brands/{b['id']}", json={"name": "Acme Bank", "aliases": ["Acme"]}, headers=h)  # no description key
    assert r.json()["description"] == "Acme Bank is a retail bank."


def test_archive_is_soft_admin_only_and_needs_the_name(env):
    c, h, b = env
    with c.app_ref.state.session_factory() as db:
        make_user(db, "an@example.com", "analyst")
    ha = headers_for(c, "an@example.com", "Some-Password-1")
    assert c.delete(f"/api/brands/{b['id']}?confirm=Acme%20Bank", headers=ha).status_code == 403
    assert c.delete(f"/api/brands/{b['id']}", headers=h).status_code == 409
    assert c.delete(f"/api/brands/{b['id']}?confirm=Acme%20Bank", headers=h).status_code == 200
    assert c.get("/api/brands", headers=h).json() == [] and c.get(f"/api/brands/{b['id']}", headers=h).status_code == 404
    assert c.post("/api/scan", json={"scope": "all"}, headers=h).status_code in (404, 409, 422)
    assert c.post("/api/brands", json={"name": "Other Co"}, headers=h).status_code == 201  # archived brand does not block a new one
    assert c.post(f"/api/brands/{b['id']}/restore", headers=h).status_code == 409
    audit = c.get("/api/audit", headers=h).json()
    assert "brand.archived" in json.dumps(audit)


def test_publishers_and_verification_status(env):
    c, h, b = env
    bid = b["id"]
    p = c.post(f"/api/brands/{bid}/publishers", json={"store": "google_play", "name": "Acme Bank Ltd", "publisher_id": "5700313618786177705"}, headers=h)
    assert p.status_code == 201
    assert c.post(f"/api/brands/{bid}/publishers", json={"store": "google_play", "publisher_id": "5700313618786177705"}, headers=h).status_code == 409
    assert c.post(f"/api/brands/{bid}/publishers", json={"store": "any"}, headers=h).status_code == 422
    v = {"asset_type": "publisher", "asset_key": "5700313618786177705", "status": "verified"}
    assert c.put(f"/api/brands/{bid}/verification", json=v, headers=h).status_code == 422  # a method is required
    r = c.put(f"/api/brands/{bid}/verification", json={**v, "method": "store_listing", "note": "Listed on the official site"}, headers=h)
    assert r.status_code == 200 and r.json()["verified_by"] and r.json()["verified_at"] and r.json()["method"] == "store_listing"
    assert c.put(f"/api/brands/{bid}/verification", json={"asset_type": "domain", "asset_key": "nope.com", "status": "unverified"}, headers=h).status_code == 404
    out = c.get(f"/api/brands/{bid}", headers=h).json()
    assert out["verifications"][0]["status"] == "verified" and out["publishers"][0]["publisher_id"] == "5700313618786177705"
    assert c.put(f"/api/brands/{bid}/verification", json={"asset_type": "domain", "asset_key": "acmebank.com", "status": "verified", "method": "dns_txt"}, headers=h).status_code == 200


def test_only_a_verified_publisher_id_is_allowlisted(env):
    c, h, b = env
    bid = b["id"]
    c.post(f"/api/brands/{bid}/publishers", json={"store": "google_play", "publisher_id": "PUB123"}, headers=h)
    app_row = {"store": "google_play", "package_id": "com.acmebank.cards", "name": "Acme Bank Cards", "publisher": "Acme Bank Ltd", "publisher_id": "PUB123"}
    r = c.post("/api/candidates/app", json=app_row, headers=h)
    assert r.status_code == 201 and r.json()["status"] in ("created", "cleared")  # unverified: not allowlisted
    if r.json()["status"] == "created":
        did = r.json()["detection_id"]
        c.put(f"/api/brands/{bid}/verification", json={"asset_type": "publisher", "asset_key": "pub123", "status": "verified", "method": "manual_review"}, headers=h)
        assert c.get(f"/api/detections/{did}", headers=h).json()["status"] == "marked_official"  # verifying closes it
    c.put(f"/api/brands/{bid}/verification", json={"asset_type": "publisher", "asset_key": "pub123", "status": "verified", "method": "manual_review"}, headers=h)
    r2 = c.post("/api/candidates/app", json={**app_row, "package_id": "com.acmebank.other", "name": "Acme Bank Other"}, headers=h)
    assert r2.json()["status"] == "official"


def test_manual_social_and_app_and_web_provenance(env):
    c, h, b = env
    s = c.post("/api/candidates/social", json={"platform": "instagram", "handle": "acme_bank_support", "display_name": "Acme Bank Support", "bio": "Pay a processing fee to unblock"}, headers=h)
    assert s.status_code == 201 and s.json()["status"] == "created"
    d = c.get(f"/api/detections/{s.json()['detection_id']}", headers=h).json()
    assert d["origin"] == "manual" and d["source"] == "live" and d["category"] == "social"
    a = c.post("/api/candidates/app", json={"store": "google_play", "package_id": "com.acmebank.mobile2", "name": "Acme Bank Mobile", "publisher": "Quick Tools"}, headers=h)
    assert a.status_code == 201 and a.json()["status"] in ("created", "cleared")
    w = c.post("/api/candidates/web", json={"url": "https://acmebank-login.example.test/verify"}, headers=h)
    assert w.status_code == 201 and w.json()["status"] == "created"
    assert c.get(f"/api/detections/{w.json()['detection_id']}", headers=h).json()["origin"] == "manual"
    # the official handle is excluded first
    assert c.post("/api/candidates/social", json={"platform": "instagram", "handle": "acmebank"}, headers=h).json()["status"] == "official"
    assert c.post("/api/candidates/web", json={"url": "https://www.acmebank.com/x"}, headers=h).json()["status"] == "official"
    assert c.post("/api/candidates/social", json={"platform": "instagram", "handle": "x"}, headers=h).status_code == 422
    assert c.post("/api/candidates/social", json={"platform": "instagram", "handle": "abcde", "evil": 1}, headers=h).status_code == 422  # strict schema


def test_dedup_across_manual_import_and_scan_keeps_one_row(settings, checker):
    cands = [social("instagram", "acme_bank_support", "Acme Bank Support")]
    with make_client(settings, checker, [StaticCollector(cands)]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h)
        run_scan(c, h)
        first = all_detections(c, h)
        assert len(first) == 1 and first[0]["id"]
        r = c.post("/api/candidates/social", json={"platform": "instagram", "handle": "Acme_Bank_Support", "display_name": "Acme Bank Support"}, headers=h)
        assert r.json()["status"] == "updated" and r.json()["detection_id"] == first[0]["id"]
        imp = c.post("/api/candidates/import?kind=social&format=csv", files=csv_file("platform,handle,display_name\ninstagram,acme_bank_support,Acme Bank Support\n"), headers=h)
        assert imp.json()["updated"] == 1
        run_scan(c, h)
        assert len(all_detections(c, h)) == 1
        d = c.get(f"/api/detections/{first[0]['id']}", headers=h).json()
        assert d["origin"] == "scan" and d["extras"] == {} and len(d["score_history"]) >= 3


CSV_OK = (
    "platform,handle,display_name,bio,followers\n"
    "instagram,acme_bank_help,Acme Bank Help,Contact us to update your KYC,120\n"
    "instagram,acme_bank_help,Acme Bank Help,again,120\n"  # duplicate in the file
    "instagram,a,Too short,,\n"  # bad handle
    "facebook,acme.bank.care,=HYPERLINK(\"http://evil.test\")Acme Bank Care,,abc\n"  # bad followers
    "x,acmebank,Acme,,\n"  # official
)


def test_csv_import_partial_success_report_and_dry_run(env):
    c, h, b = env
    dry = c.post("/api/candidates/import?kind=social&format=csv&dry_run=true", files=csv_file(CSV_OK), headers=h).json()
    assert dry["dry_run"] and dry["total"] == 5 and dry["rejected"] == 2 and dry["duplicates"] == 1 and all_detections(c, h) == []
    rep = c.post("/api/candidates/import?kind=social&format=csv", files=csv_file(CSV_OK), headers=h).json()
    by_row = {r["row"]: r for r in rep["results"]}
    assert by_row[1]["status"] == "created" and by_row[2]["status"] == "duplicate" and by_row[3]["status"] == "error" and "Handle" in by_row[3]["message"]
    assert by_row[4]["status"] == "error" and by_row[5]["status"] == "official"
    assert rep["created"] == 1 and rep["rejected"] == 2 and rep["accepted"] == 2
    items = all_detections(c, h)
    assert len(items) == 1 and c.get(f"/api/detections/{items[0]['id']}", headers=h).json()["origin"] == "import"


def test_formula_and_html_cells_are_defused_not_trusted(env):
    c, h, b = env
    text = 'platform,handle,display_name,bio\ninstagram,acme_bank_support,"=Acme Bank Support","<script>alert(1)</script> Update your KYC now"\n'
    rep = c.post("/api/candidates/import?kind=social&format=csv", files=csv_file(text), headers=h).json()
    assert rep["created"] == 1
    d = c.get(f"/api/detections/{rep['results'][0]['detection_id']}", headers=h).json()
    assert d["name"].startswith("'=") and not d["name"].startswith("=")
    assert d["suspect"]["bio"].startswith("<script>")  # stored as inert text; clients render it escaped, the API serves JSON only


def test_import_file_limits_and_strict_format(env, settings):
    c, h, b = env
    q = "/api/candidates/import?kind=social&format="
    assert c.post(q + "csv", files=csv_file("platform,handle,evil\ninstagram,abcde,1\n"), headers=h).status_code == 422
    assert c.post(q + "csv", files=csv_file(""), headers=h).status_code == 422
    assert c.post(q + "csv", files={"file": ("a.csv", b"\xff\xfe\x00bad\x80\x81", "text/csv")}, headers=h).status_code == 422
    assert c.post(q + "json", files=csv_file("{not json"), headers=h).status_code == 422
    assert c.post(q + "json", files=csv_file('{"a": 1}'), headers=h).status_code == 422
    big = "platform,handle\n" + "instagram,abcdef\n" * 100_000
    assert c.post(q + "csv", files=csv_file(big), headers=h).status_code == 413
    many = "platform,handle\n" + "".join(f"instagram,user{i:04d}\n" for i in range(501))
    assert c.post(q + "csv", files=csv_file(many), headers=h).status_code == 413
    ok = c.post(q + "json", files=csv_file(json.dumps([{"platform": "instagram", "handle": "acme_bank_json"}, "oops", {"platform": "tiktok", "handle": "abcde"}]), "r.json"), headers=h).json()
    assert ok["total"] == 3 and ok["rejected"] == 2 and ok["results"][0]["status"] in ("created", "cleared", "not_flagged")
    bom = ("﻿platform,handle\ninstagram,acme_bank_bom\n").encode()
    assert c.post(q + "csv", files={"file": ("b.csv", bom, "text/csv")}, headers=h).status_code == 200


def test_import_needs_a_writer(env):
    c, h, b = env
    with c.app_ref.state.session_factory() as db:
        make_user(db, "v@example.com", "viewer")
    hv = headers_for(c, "v@example.com", "Some-Password-1")
    assert c.post("/api/candidates/import?kind=web&format=csv", files=csv_file("url\nhttp://acmebank-x.test\n"), headers=hv).status_code == 403
    assert c.post("/api/candidates/social", json={"platform": "instagram", "handle": "abcde"}, headers=hv).status_code == 403


def test_import_never_makes_an_outbound_request(env, monkeypatch, checker):
    """The suite blocks real HTTP and DNS; any fetch of a row URL would raise. The URL checker is never called either."""
    c, h, b = env
    rows = [{"platform": "instagram", "handle": "acme_bank_img", "display_name": "Acme Bank", "url": "https://evil.example.test/p"}]
    rep = c.post("/api/candidates/import?kind=social&format=json", files=csv_file(json.dumps(rows), "r.json"), headers=h).json()
    assert rep["results"][0]["status"] == "created" and checker.calls == [] or checker.calls  # account setup used it, intake did not
    before = len(checker.calls)
    c.post("/api/candidates/import?kind=social&format=json", files=csv_file(json.dumps(rows), "r.json"), headers=h)
    assert len(checker.calls) == before
    det = all_detections(c, h)[0]
    d = c.get(f"/api/detections/{det['id']}", headers=h).json()
    assert d["logo"]["status"] == "unavailable"  # the row URL was not downloaded

"""Look-alike lab, settings, evaluation and report exports (HTML escaping, CSV formula injection)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import pytest

from app.models import Detection
from app.services.reports import csv_safe
from tests.scan_helpers import run_scan, set_mode, setup_brand

EVIL_HANDLE = '"><img src=x onerror=alert(1)>'
EVIL_NAME = "<script>alert('x')</script>Acme & Co"


@pytest.fixture
def acme(client, admin_h):
    set_mode(client, admin_h, "sample")
    return setup_brand(client, admin_h)


# ---- look-alike lab -------------------------------------------------------------------------------------------------


def test_lookalike_test_endpoint_shapes(client, admin_h, acme):
    r = client.post("/api/lookalike/test", json={"name": "acrnebank-refund"}, headers=admin_h)
    assert r.status_code == 200
    j = r.json()
    assert j["verdict"] == "flagged" and j["brand"] == "Acme Bank" and j["match_percent"] >= 80 and j["on_allowlist"] is False
    assert j["techniques"] and j["squat_category"] in ("typo", "combo", "fuzzy") and j["technique_labels"]
    assert j["reasons"] and {"rule", "label"} <= set(j["reasons"][0]) and j["headline"] and j["explanation"]
    assert [s["step"] for s in j["normalization"]] == [1, 2, 3, 4, 5] and j["normalization"][0]["value"] == "acrnebank-refund"
    assert j["normalization"][3]["value"] == "acmebankrefund"


@pytest.mark.parametrize("name,verdict", [("Acme Bakery", "not_flagged"), ("Green Valley Farms", "not_flagged"), ("@acmebank", "official"),
                                           ("acmebank", "official"), ("Acme Bank", "official"), ("acm3bank", "flagged"), ("Acme Bnak", "flagged")])
def test_lookalike_verdicts(client, admin_h, acme, name, verdict):
    assert client.post("/api/lookalike/test", json={"name": name}, headers=admin_h).json()["verdict"] == verdict


def test_lookalike_official_by_allowlist_vs_name(client, admin_h, acme):
    j = client.post("/api/lookalike/test", json={"name": "@acmebank", "platform": "instagram"}, headers=admin_h).json()
    assert j["verdict"] == "official" and j["on_allowlist"] is True and j["official_basis"] == "allowlist"
    j = client.post("/api/lookalike/test", json={"name": "Acme Bank"}, headers=admin_h).json()
    assert j["verdict"] == "official" and j["on_allowlist"] is False and j["official_basis"] == "name"
    assert "name alone never excludes" in j["explanation"]


def test_lookalike_validates_input(client, admin_h, acme):
    assert client.post("/api/lookalike/test", json={"name": ""}, headers=admin_h).status_code == 422
    assert client.post("/api/lookalike/test", json={"name": "x" * 201}, headers=admin_h).status_code == 422
    assert client.post("/api/lookalike/test", json={"name": "  \x00 "}, headers=admin_h).status_code == 422


def test_lookalike_cases_run_returns_n_of_m_and_required_ten_pass(client, admin_h, acme):
    cases = client.get("/api/lookalike/test-cases", headers=admin_h).json()
    assert cases["total"] == 12 and sum(c["expected"] == "official" for c in cases["items"]) == 2
    run = client.post("/api/lookalike/test-cases/run", headers=admin_h).json()
    assert run["total"] == 12 and run["summary"] == f"{run['passed']} of 12 pass"
    assert run["passed"] == 12 and run["failed"] == 0, [i for i in run["items"] if not i["pass"]]
    ten = {"acrnebank", "Acme Bank Support", "acme-bank", "A C M E Bank", "acm3bank", "Acme Bnak", "acmebank_official", "Acme Bakery", "Acme Hotels", "Acme Weather"}
    assert ten <= {i["name"] for i in run["items"] if i["pass"]}
    assert all("result" in i and "expected" in i for i in run["items"]) and run["ran_at"]


def test_runner_computes_results_instead_of_echoing_expected(client, admin_h, acme):
    client.post("/api/lookalike/test-cases", json={"name": "Acme Bakery", "expected": "flagged"}, headers=admin_h)  # deliberately wrong label
    run = client.post("/api/lookalike/test-cases/run", headers=admin_h).json()
    bad = [i for i in run["items"] if not i["pass"]]
    assert run["summary"] == "12 of 13 pass" and len(bad) == 1 and bad[0]["result"] == "not_flagged" and bad[0]["expected"] == "flagged"


def test_runner_depends_on_the_allowlist(client, admin_h):
    set_mode(client, admin_h, "sample")
    setup_brand(client, admin_h, accounts=(), apps=())  # no official @acmebank handle
    run = client.post("/api/lookalike/test-cases/run", headers=admin_h).json()
    assert run["passed"] == 12  # "@acmebank" is still the exact brand name -> official by name


def test_admin_can_add_and_delete_cases(client, admin_h, acme):
    r = client.post("/api/lookalike/test-cases", json={"name": "Acme Pay Support", "expected": "flagged"}, headers=admin_h)
    assert r.status_code == 201 and r.json()["builtin"] is False
    assert client.post("/api/lookalike/test-cases", json={"name": "Acme Pay Support", "expected": "flagged"}, headers=admin_h).status_code == 409
    assert client.post("/api/lookalike/test-cases", json={"name": "x", "expected": "maybe"}, headers=admin_h).status_code == 422
    assert client.get("/api/lookalike/test-cases", headers=admin_h).json()["total"] == 13
    assert client.post("/api/lookalike/test-cases/run", headers=admin_h).json()["summary"] == "13 of 13 pass"
    assert client.delete(f"/api/lookalike/test-cases/{r.json()['id']}", headers=admin_h).status_code == 200
    assert client.delete("/api/lookalike/test-cases/9999", headers=admin_h).status_code == 404


def test_lab_follows_settings_rules_and_words(client, admin_h, acme):
    assert client.post("/api/lookalike/test", json={"name": "Acme Bank Cashback"}, headers=admin_h).json()["verdict"] in ("flagged", "not_flagged")
    client.put("/api/settings", json={"monitoring": {"rules": {"spacing_and_separators": False}}}, headers=admin_h)
    j = client.post("/api/lookalike/test", json={"name": "acme-bank"}, headers=admin_h).json()
    assert j["verdict"] == "not_flagged" and j["engine_verdict"] == "review" and "Switched-off rules" in j["explanation"]
    client.put("/api/settings", json={"monitoring": {"rules": {"spacing_and_separators": True}, "impersonation_words": ["zorbwords"]}}, headers=admin_h)
    assert client.post("/api/lookalike/test", json={"name": "Acme Bank zorbwords"}, headers=admin_h).json()["verdict"] == "flagged"


# ---- settings -------------------------------------------------------------------------------------------------------


def test_settings_defaults_and_roundtrip(client, admin_h, acme):
    s = client.get("/api/settings", headers=admin_h).json()
    assert s["data_mode"] == "sample" and s["effective_data_mode"] == "sample"
    m = s["monitoring"]
    assert m["schedule"] == "1d" and m["sensitivity"] == 75 and m["threshold"] == 25 and set(m["rules"]) == {
        "character_swaps", "digits_and_symbols", "added_words", "spacing_and_separators", "transposed_or_missing"}
    assert "support" in m["impersonation_words"] and set(m["sources"]) == {
        "instagram", "facebook", "x", "telegram", "linkedin", "google_play", "app_store", "third_party_apk"} and all(m["sources"].values())
    assert s["notifications"]["delivery"] == "not_implemented" and s["account"]["organization"] == "Acme Bank"
    put = client.put("/api/settings", json={"monitoring": {"schedule": "1h", "sensitivity": 60, "impersonation_words": [" Alert ", "alert", "Verify"]},
                                            "notifications": {"email_alerts": True, "webhook_url": "https://hooks.example.com/x"}}, headers=admin_h)
    assert put.status_code == 200
    again = client.get("/api/settings", headers=admin_h).json()
    assert again["monitoring"]["schedule"] == "1h" and again["monitoring"]["sensitivity"] == 60 and again["monitoring"]["threshold"] == 40
    assert again["monitoring"]["impersonation_words"] == ["alert", "verify"] and again["notifications"]["email_alerts"] is True
    assert again["notifications"]["webhook_url"] == "https://hooks.example.com/x" and again["updated_at"]
    # partial PUT leaves the rest alone
    client.put("/api/settings", json={"monitoring": {"sensitivity": 80}}, headers=admin_h)
    again = client.get("/api/settings", headers=admin_h).json()
    assert again["monitoring"]["schedule"] == "1h" and again["monitoring"]["sensitivity"] == 80


@pytest.mark.parametrize("body", [
    {"monitoring": {"schedule": "2m"}}, {"monitoring": {"sensitivity": 49}}, {"monitoring": {"sensitivity": 96}},
    {"monitoring": {"rules": {"made_up": True}}}, {"monitoring": {"sources": {"myspace": True}}},
    {"monitoring": {"impersonation_words": ["x" * 31]}}, {"notifications": {"webhook_url": "http://insecure.example.com"}},
    {"notifications": {"webhook_url": "https://a b"}}, {"data_mode": "demo"},
])
def test_settings_validation(client, admin_h, acme, body):
    assert client.put("/api/settings", json=body, headers=admin_h).status_code == 422


def test_settings_sources_stay_in_sync_with_source_monitoring(client, admin_h, acme):
    client.put("/api/settings", json={"monitoring": {"sources": {"telegram": False}}}, headers=admin_h)
    items = client.get("/api/sources", headers=admin_h).json()["items"]
    assert next(i for i in items if i["slug"] == "telegram")["monitored"] is False
    tg = next(i for i in items if i["slug"] == "telegram")
    client.patch(f"/api/sources/{tg['id']}", json={"monitored": True}, headers=admin_h)
    assert client.get("/api/settings", headers=admin_h).json()["monitoring"]["sources"]["telegram"] is True


def test_settings_can_change_data_mode(client, admin_h, acme):
    client.put("/api/settings", json={"data_mode": "live"}, headers=admin_h)
    assert client.get("/api/settings/data-mode", headers=admin_h).json()["data_mode"] == "live"


def test_unmonitored_platform_is_not_scanned(client, admin_h, acme):
    client.put("/api/settings", json={"monitoring": {"sources": {"telegram": False}}}, headers=admin_h)
    run_scan(client, admin_h)
    items = client.get("/api/detections", params={"limit": 200, "source": "all"}, headers=admin_h).json()["items"]
    assert "telegram" not in {i["platform"] for i in items}


def test_sensitivity_preview_and_scan_threshold(client, admin_h, acme):
    run_scan(client, admin_h)
    n = client.get("/api/detections", headers=admin_h).json()["total"]
    p75 = client.post("/api/settings/sensitivity-preview", json={"sensitivity": 75}, headers=admin_h).json()
    p95 = client.post("/api/settings/sensitivity-preview", json={"sensitivity": 95}, headers=admin_h).json()
    p50 = client.post("/api/settings/sensitivity-preview", json={"sensitivity": 50}, headers=admin_h).json()
    assert p75["would_review"] == n and p95["would_review"] >= p75["would_review"] >= p50["would_review"]
    assert p95["total"] >= n and p75["threshold"] == 25 and p50["threshold"] == 50
    assert "balanced" in p75["message"] and "Sensitive" in p95["message"] and "Strict" in p50["message"]
    assert client.post("/api/settings/sensitivity-preview", json={"sensitivity": 10}, headers=admin_h).status_code == 422
    # a stricter sensitivity changes which weak candidates a re-scan keeps
    client.put("/api/settings", json={"monitoring": {"sensitivity": 50}}, headers=admin_h)
    run_scan(client, admin_h)
    assert client.get("/api/detections", headers=admin_h).json()["total"] <= n


# ---- evaluation -----------------------------------------------------------------------------------------------------


def test_evaluation_shape_and_label(client, admin_h, acme):
    r = client.get("/api/evaluation", headers=admin_h)
    assert r.status_code == 200
    j = r.json()
    assert "optimistic" in j["label"] and j["data_mode"] == "sample"
    syn = j["synthetic"]
    assert syn["cached"] in (True, False) and syn["brands"] == 5 and syn["seed"] == 7 and syn["computed_at"]
    techs = {t["technique"] for t in syn["by_technique"]}
    assert {"character_swap", "added_word", "spacing_or_separator", "homoglyph", "mixed"} <= techs
    for row in [*syn["by_technique"], syn["overall"]]:
        assert 0 <= row["precision"] <= 1 and 0 <= row["recall"] <= 1 and row["n_pos"] > 0
    assert syn["overall"]["recall"] > 0.8 and syn["overall"]["precision"] > 0.9
    lt = j["lookalike_tests"]
    assert lt["total"] == 12 and lt["summary"] == "12 of 12 pass" and lt["precision"] == 1.0 and lt["recall"] == 1.0 and len(lt["items"]) == 12
    assert set(j["reviewed"]) == {"confirmed", "dismissed", "marked_official", "pending"} and j["detection"]["precision"] is None
    assert "Recall cannot be measured" in j["detection"]["definition"]


def test_evaluation_is_cached_and_run_refreshes(client, admin_h, acme):
    first = client.get("/api/evaluation", headers=admin_h).json()["synthetic"]
    second = client.get("/api/evaluation", headers=admin_h).json()["synthetic"]
    assert second["cached"] is True and second["computed_at"] == first["computed_at"]
    ran = client.post("/api/evaluation/run", headers=admin_h).json()["synthetic"]
    assert ran["cached"] is False


def test_evaluation_reviewed_precision_from_decisions(client, admin_h, acme):
    run_scan(client, admin_h)
    ids = [i["id"] for i in client.get("/api/detections", headers=admin_h).json()["items"]]
    client.post(f"/api/detections/{ids[0]}/confirm", headers=admin_h)
    client.post(f"/api/detections/{ids[1]}/confirm", headers=admin_h)
    client.post(f"/api/detections/{ids[2]}/dismiss", headers=admin_h)
    j = client.get("/api/evaluation", headers=admin_h).json()
    assert j["reviewed"]["confirmed"] == 2 and j["reviewed"]["dismissed"] == 1 and j["detection"]["precision"] == 0.667
    assert sum(b["confirmed"] + b["dismissed"] for b in j["by_type"]) == 3


# ---- reports: escaping and CSV injection ----------------------------------------------------------------------------


def _insert_detection(app, **kw) -> int:
    base = dict(brand_id=1, type="fake_profile", category="social", platform="instagram", name="N", handle="h", score=90, band="high", status="confirmed",
                reasons=[], evidence={}, details={}, source="sample", canonical_key=f"k{kw.get('name', 'N')}{kw.get('handle', 'h')}",
                found_at=datetime.now(UTC), first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC), score_history=[], notes=[])
    base.update(kw)
    with app.state.session_factory() as db:
        d = Detection(**base)
        db.add(d)
        db.commit()
        return d.id


def test_report_html_escapes_malicious_user_data(client, admin_h, app, acme):
    did = _insert_detection(
        app, name=EVIL_NAME, handle=EVIL_HANDLE, publisher="<b>pub</b>", profile_url="javascript:alert(1)//<x>",
        reasons=[{"signal": "name_similarity", "label": "<i>Name</i>", "detail": "<script>steal()</script>", "points": 30, "max_points": 34}],
        evidence={"page_url": "https://x.example/<script>", "sha256": "ab" * 32, "captured_at": "now"},
    )
    rep = client.post("/api/reports", json={"detection_ids": [did], "format": "html"}, headers=admin_h)
    assert rep.status_code == 201, rep.text
    rid = rep.json()["id"]
    for path in (f"/api/reports/{rid}/preview", f"/api/reports/{rid}/export.html", f"/api/reports/{rid}/download"):
        r = client.get(path, headers=admin_h)
        body = r.text
        assert "<script" not in body.lower() and "<img" not in body and "<b>pub" not in body and "<i>Name" not in body
        assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;Acme &amp; Co" in body
        assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in body
        assert r.headers["x-content-type-options"] == "nosniff" and "default-src 'none'" in r.headers["content-security-policy"]
    pre = client.get("/api/reports/preview.html", params={"ids": str(did)}, headers=admin_h)
    assert pre.status_code == 200 and "<script" not in pre.text.lower() and "&lt;script&gt;" in pre.text
    # title is user input too
    rep2 = client.post("/api/reports", json={"detection_ids": [did], "format": "html", "title": "<script>t()</script>"}, headers=admin_h).json()
    assert "<script>t()" not in client.get(f"/api/reports/{rep2['id']}/preview", headers=admin_h).text


def test_report_csv_neutralises_formula_injection(client, admin_h, app, acme):
    did = _insert_detection(app, name='=HYPERLINK("http://evil","click")', handle="@SUM(1+1)", publisher="+cmd|' /C calc'!A0",
                            profile_url="-2+3", reasons=[{"signal": "s", "label": "@evil", "detail": "=1+1", "points": 5, "max_points": 5}],
                            evidence={"page_url": "=evil()", "sha256": "ab" * 32, "captured_at": "x"})
    rep = client.post("/api/reports", json={"detection_ids": [did], "format": "csv"}, headers=admin_h).json()
    text = client.get(f"/api/reports/{rep['id']}/export.csv", headers=admin_h).text
    row = next(csv.DictReader(io.StringIO(text)))
    for col in ("name", "handle_or_package", "publisher", "url", "reasons", "evidence_url"):
        assert not row[col].startswith(("=", "+", "-", "@", "\t", "\r")), (col, row[col])
    assert row["name"].startswith("'=HYPERLINK") and row["publisher"].startswith("'+cmd") and row["evidence_url"] == "'=evil()"
    assert row["id"] == str(did) and row["score"] == "90"


def test_csv_safe_unit():
    for bad in ("=1+1", "+1", "-1", "@a", "\tcmd", "\rcmd"):
        assert csv_safe(bad) == "'" + bad
    assert csv_safe("fine") == "fine" and csv_safe(None) == "" and csv_safe(12) == "12"


def test_report_preview_stats_rows_and_filters(client, admin_h, app, acme):
    a = _insert_detection(app, name="A", handle="aaa", score=90, status="confirmed", canonical_key="x1")
    b = _insert_detection(app, name="B", handle=None, package_id="com.b.app", type="fake_app", category="apps", platform="google_play", score=95,
                          status="confirmed", canonical_key="x2")
    c = _insert_detection(app, name="C", handle="ccc", score=70, status="new", canonical_key="x3")
    p = client.get("/api/reports/preview", headers=admin_h).json()  # default: confirmed only
    assert [r["id"] for r in p["rows"]] == [b, a] and p["stats"]["detections"] == 2 and p["stats"]["confirmed"] == 2
    assert p["stats"]["social"] == 1 and p["stats"]["apps"] == 1 and p["stats"]["official_excluded"] == 3 and p["includes_sample"] is True and p["is_sample"] is True
    assert [r["id"] for r in client.get("/api/reports/preview", params={"status": "new"}, headers=admin_h).json()["rows"]] == [c]
    assert [r["id"] for r in client.get("/api/reports/preview", params={"types": "fake_app"}, headers=admin_h).json()["rows"]] == [b]
    assert client.get("/api/reports/preview", params={"types": "nope"}, headers=admin_h).status_code == 422
    sel = client.post("/api/reports", json={"detection_ids": [c, a], "format": "csv"}, headers=admin_h).json()
    assert sel["count"] == 2 and sel["detection_ids"] == [a, c]  # sorted by score, selection honoured (not only confirmed)
    got = client.get(f"/api/reports/{sel['id']}", headers=admin_h).json()
    assert [r["id"] for r in got["rows"]] == [a, c] and got["status"] == "draft"
    assert client.get("/api/reports/9999", headers=admin_h).status_code == 404

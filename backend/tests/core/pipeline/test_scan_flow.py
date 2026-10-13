"""End-to-end with the sample collector: scan -> detections -> actions -> report. No network."""

from __future__ import annotations

import hashlib

import pytest

from app.models import AuditLog, Detection, OfficialMatch, Scan
from tests.scan_helpers import all_detections, run_scan, set_mode, setup_brand


@pytest.fixture
def sample(client, admin_h):
    set_mode(client, admin_h, "sample")
    return setup_brand(client, admin_h)


def test_scan_creates_detections_and_reports_counts(client, admin_h, sample):
    scan = run_scan(client, admin_h)
    assert scan["status"] == "completed" and scan["state"] == "done"
    assert scan["started_by"] == "Test Admin" and scan["is_sample"] is True
    assert scan["counts"]["detections_new"] > 0 and scan["counts"]["official_excluded"] >= 2
    assert scan["source_report"][0]["collector"] == "sample"
    assert set(scan["timings"]) == {"collect_ms", "process_ms", "total_ms"}
    assert scan["errors"] == []
    items = all_detections(client, admin_h)
    assert len(items) == scan["counts"]["detections_new"]
    assert all(i["source"] == "sample" for i in items)  # sample data is labelled as such
    assert all(i["status"] == "new" for i in items)  # humans approve every detection
    for i in items:  # every score is explainable: reasons sum to raw total
        d = client.get(f"/api/detections/{i['id']}", headers=admin_h).json()
        assert sum(r["points"] for r in d["reasons"]) == d["raw_total"]
        assert min(100, d["raw_total"]) == d["score"]
        assert all(r["detail"] for r in d["reasons"])


def test_job_polling_and_latest_scan(client, admin_h, sample):
    scan = run_scan(client, admin_h)
    job = client.get(f"/api/jobs/{scan['job_id']}", headers=admin_h).json()
    assert job["id"] == scan["id"] and job["state"] == "done" and job["progress"] == 100
    assert job["result"]["new_detections"] == scan["counts"]["detections_new"]
    assert client.get("/api/jobs/nope", headers=admin_h).status_code == 404
    latest = client.get("/api/scans/latest", headers=admin_h).json()["scan"]
    assert latest["id"] == scan["id"]
    lst = client.get("/api/scans", headers=admin_h).json()
    assert lst["total"] == 1 and lst["items"][0]["job_id"] == scan["job_id"]
    assert client.get(f"/api/scans/{scan['id']}", headers=admin_h).json()["status"] == "completed"
    st = client.get("/api/scan/status", headers=admin_h).json()
    assert st["running_job_id"] is None and st["last_scan_at"] and st["is_sample"] is True


def test_official_assets_never_listed_and_are_excluded(client, admin_h, sample):
    run_scan(client, admin_h)
    official = {"acmebank", "com.acmebank.mobile"}
    for params in ({}, {"status": "marked_official"}, {"source": "all"}, {"category": "social"}, {"category": "apps"}):
        for i in all_detections(client, admin_h, **params):
            assert (i["handle"] or "").lstrip("@").lower() not in official
            assert i["package_id"] not in official
    fp = client.get("/api/detections/false-positives", headers=admin_h).json()["items"]
    assert all((i["handle"] or "").lstrip("@") != "acmebank" for i in fp)
    dash = client.get("/api/dashboard", headers=admin_h).json()
    for row in dash["needs_review"] + dash["recent"]:
        assert row["name"].lstrip("@").lower() not in official
    excluded = client.get("/api/detections/excluded", headers=admin_h).json()["items"]
    labels = " ".join(i["label"] for i in excluded)
    assert "acmebank on instagram" in labels and "com.acmebank.mobile" in labels
    assert any(i["times_seen"] >= 1 for i in excluded)
    assert [i["kind"] for i in client.get("/api/detections/excluded", params={"category": "apps"}, headers=admin_h).json()["items"]] == ["app"]


def test_official_match_rows_record_reason_and_rule(client, admin_h, app, sample):
    run_scan(client, admin_h)
    with app.state.session_factory() as db:
        rows = db.query(OfficialMatch).all()
    assert rows and all(r.reason and r.matched_rule and r.match_key for r in rows)
    assert {r.reason for r in rows} >= {"exact_handle", "package_id"}


def test_mark_official_moves_asset_to_allowlist_and_rescan_drops_it(client, admin_h, app, sample):
    run_scan(client, admin_h)
    target = next(i for i in all_detections(client, admin_h, category="social") if i["handle"])
    r = client.post(f"/api/detections/{target['id']}/mark-official", json={"note": "our regional account"}, headers=admin_h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detection"]["status"] == "marked_official" and body["allowlist_entry"]["kind"] == "account"
    assert body["official_assets"] == 4 and body["audit_id"]
    accounts = client.get(f"/api/brands/{sample['id']}/accounts", headers=admin_h).json()["items"]
    assert any(a["platform"] == target["platform"] and a["handle"].lstrip("@") == target["handle"].lstrip("@") for a in accounts)
    assert target["id"] not in [i["id"] for i in all_detections(client, admin_h, status="marked_official")]
    assert target["id"] not in [i["id"] for i in all_detections(client, admin_h)]
    excluded = " ".join(i["label"] for i in client.get("/api/detections/excluded", headers=admin_h).json()["items"])
    assert target["handle"].lstrip("@") in excluded
    # a re-scan drops it before scoring: no new detection, an official match instead
    scan2 = run_scan(client, admin_h)
    assert target["id"] not in [i["id"] for i in all_detections(client, admin_h)]
    assert scan2["counts"]["official_excluded"] >= 3
    # a second mark-official is refused
    assert client.post(f"/api/detections/{target['id']}/mark-official", json={}, headers=admin_h).status_code == 409
    with app.state.session_factory() as db:
        acts = [a.action for a in db.query(AuditLog).all()]
    assert "detection.marked_official" in acts and "account.added" in acts


def test_mark_official_for_third_party_apk_is_refused(client, admin_h, sample):
    run_scan(client, admin_h)
    apk = [i for i in all_detections(client, admin_h, category="apps") if i["platform"] == "third_party_apk"]
    if not apk:
        pytest.skip("sample produced no APK listing")
    assert client.post(f"/api/detections/{apk[0]['id']}/mark-official", json={}, headers=admin_h).status_code == 422


def test_human_decisions_survive_rescan(client, admin_h, app, sample):
    run_scan(client, admin_h)
    items = all_detections(client, admin_h, sort="score_desc")
    confirm, dismiss, review = items[0], items[1], items[2]
    assert client.post(f"/api/detections/{confirm['id']}/confirm", json={"note": "real"}, headers=admin_h).status_code == 200
    assert client.post(f"/api/detections/{dismiss['id']}/dismiss", json={"note": "not us"}, headers=admin_h).status_code == 200
    assert client.post(f"/api/detections/{review['id']}/keep-reviewing", headers=admin_h).status_code == 200
    # make the stored score differ materially from what the re-scan will compute
    with app.state.session_factory() as db:
        d = db.get(Detection, dismiss["id"])
        d.score = 5
        db.commit()
    scan2 = run_scan(client, admin_h)
    assert scan2["counts"]["detections_protected"] >= 3
    after = {i["id"]: i for i in all_detections(client, admin_h)}
    assert after[confirm["id"]]["status"] == "confirmed"
    assert after[dismiss["id"]]["status"] == "dismissed"
    assert after[review["id"]]["status"] == "reviewing"
    assert after[dismiss["id"]]["score"] == 5  # not rewritten by the re-scan
    d = client.get(f"/api/detections/{dismiss['id']}", headers=admin_h).json()
    assert len(d["score_history"]) == 2
    assert any("Re-scan scored this" in n["text"] for n in d["notes"])  # material change -> note, not reopened
    assert d["status"] == "dismissed"
    assert d["last_seen_at"] >= d["first_seen_at"]
    # untouched new ones are refreshed, not duplicated
    assert len(after) == len(items)


def test_status_actions_are_audited_and_validated(client, admin_h, app, sample):
    run_scan(client, admin_h)
    det = all_detections(client, admin_h)[0]
    r = client.post(f"/api/detections/{det['id']}/status", json={"status": "confirmed", "note": "checked bio"}, headers=admin_h)
    assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["audit_id"]
    hist = r.json()["history"]
    assert hist[-1]["from"] == "new" and hist[-1]["to"] == "confirmed" and hist[-1]["note"] == "checked bio" and hist[-1]["actor"] == "Test Admin"
    assert client.post(f"/api/detections/{det['id']}/status", json={"status": "new"}, headers=admin_h).status_code == 422
    assert client.post(f"/api/detections/{det['id']}/status", json={"status": "marked_official"}, headers=admin_h).status_code == 422
    assert client.post("/api/detections/99999/status", json={"status": "confirmed"}, headers=admin_h).status_code == 404
    with app.state.session_factory() as db:
        row = db.query(AuditLog).filter(AuditLog.action == "detection.status_changed").one()
    assert row.actor == "Test Admin" and "new -> confirmed" in row.detail and "checked bio" in row.detail


def test_evidence_hash_matches_stored_snapshot(client, admin_h, app, settings, sample):
    run_scan(client, admin_h)
    det = all_detections(client, admin_h)[0]
    d = client.get(f"/api/detections/{det['id']}", headers=admin_h).json()
    ev = d["evidence"]
    assert ev["sha256"] and ev["page_url"] and ev["captured_at"] and ev["screenshot_url"] is None  # screenshots off by default
    bundle = client.get(f"/api/detections/{det['id']}/evidence", headers=admin_h).json()
    assert bundle["hash_verified"] is True and bundle["snapshot"]["handle"]
    with app.state.session_factory() as db:
        stored = db.get(Detection, det["id"]).evidence
    raw = (settings.data_dir / stored["snapshot_path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ev["sha256"] == stored["snapshot_path"].split("/")[-1].removesuffix(".json")
    assert client.get(f"/api/detections/{det['id']}/evidence/screenshot", headers=admin_h).status_code == 404
    # tampering is detected
    (settings.data_dir / stored["snapshot_path"]).write_bytes(raw + b" ")
    assert client.get(f"/api/detections/{det['id']}/evidence", headers=admin_h).json()["hash_verified"] is False


def test_rescan_with_unchanged_data_keeps_one_snapshot_and_adds_history_point(client, admin_h, sample):
    run_scan(client, admin_h)
    det = all_detections(client, admin_h)[0]
    sha1 = client.get(f"/api/detections/{det['id']}", headers=admin_h).json()["evidence"]["sha256"]
    run_scan(client, admin_h)
    d = client.get(f"/api/detections/{det['id']}", headers=admin_h).json()
    assert d["evidence"]["sha256"] == sha1 and d["evidence"]["history"] == []
    assert len(d["score_history"]) == 2


def test_cleared_list_is_populated_and_not_in_detection_lists(client, admin_h, sample):
    run_scan(client, admin_h)
    cleared = client.get("/api/detections/false-positives", headers=admin_h).json()
    assert cleared["total"] > 0 and cleared["data_mode"] == "sample"
    names = [i["name"] for i in cleared["items"]]
    assert any(n.startswith("Acme ") for n in names)
    assert all(i["reason"] and i["status"] == "not_flagged" for i in cleared["items"])
    listed = {i["name"] for i in all_detections(client, admin_h)}
    assert not set(names) & listed
    assert all(i["category"] == "social" for i in client.get("/api/detections/false-positives", params={"category": "social"}, headers=admin_h).json()["items"])


def test_report_flow_draft_approve_export(client, admin_h, app, sample):
    run_scan(client, admin_h)
    items = all_detections(client, admin_h)
    ids = [items[0]["id"], items[1]["id"]]
    for i in ids:
        client.post(f"/api/detections/{i}/confirm", headers=admin_h)
    r = client.post("/api/reports", json={"format": "csv"}, headers=admin_h)  # default selection = confirmed
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "draft" and sorted(rep["detection_ids"]) == sorted(ids) and rep["includes_sample"] is True
    assert rep["filename"].endswith(".csv") and rep["size_bytes"] > 0
    csv_text = client.get(f"/api/reports/{rep['id']}/export.csv", headers=admin_h).text
    assert csv_text.splitlines()[0].startswith("id,name,handle_or_package")
    assert "evidence_sha256" in csv_text.splitlines()[0]
    html = client.get(f"/api/reports/{rep['id']}/preview", headers=admin_h).text
    assert "DRAFT" in html and "sample data" in html.lower()
    # approving is a human action, audited; nothing is sent anywhere
    ap = client.post(f"/api/reports/{rep['id']}/approve", headers=admin_h)
    assert ap.status_code == 200 and ap.json()["status"] == "approved" and ap.json()["approved_by"] == "Test Admin"
    assert client.post(f"/api/reports/{rep['id']}/approve", headers=admin_h).status_code == 409
    assert "DRAFT" not in client.get(f"/api/reports/{rep['id']}/preview", headers=admin_h).text
    with app.state.session_factory() as db:
        acts = [a.action for a in db.query(AuditLog).all()]
    assert "report.generated" in acts and "report.approved" in acts
    assert client.get("/api/reports", headers=admin_h).json()["total"] == 1
    assert client.get(f"/api/reports/{rep['id']}/download", headers=admin_h).headers["content-disposition"].startswith("attachment")


def test_report_rejects_pdf_cleared_and_empty(client, admin_h, app, sample):
    run_scan(client, admin_h)
    assert client.post("/api/reports", json={"format": "pdf"}, headers=admin_h).status_code == 422
    assert client.post("/api/reports", json={"format": "csv"}, headers=admin_h).status_code == 422  # nothing confirmed yet
    with app.state.session_factory() as db:
        cleared = db.query(Detection).filter(Detection.status == "cleared").first()
    assert client.post("/api/reports", json={"detection_ids": [cleared.id]}, headers=admin_h).status_code == 422
    assert client.post("/api/reports", json={"detection_ids": [99999]}, headers=admin_h).status_code == 422


def test_scan_row_counts_reflect_running_state(client, admin_h, app, sample):
    run_scan(client, admin_h)
    with app.state.session_factory() as db:
        s = db.query(Scan).one()
    assert s.state == "completed" and s.finished_at and s.started_at

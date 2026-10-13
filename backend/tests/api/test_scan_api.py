"""Scan start/duplicate handling, job dispatch, scheduler, list filters, dashboard honesty and the 401/403 matrix."""

from __future__ import annotations

import sys
import threading
import time
import types
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from app.models import ApiUsage, AuditLog, Scan
from app.services.jobs import JobDispatcher, resolve_executor
from app.services.scheduler import decide_schedule, next_month_start, scheduler_tick
from tests.conftest import ADMIN, headers_for
from tests.scan_helpers import StaticCollector, all_detections, make_client, run_scan, set_mode, setup_brand, social

UTC_NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def sample(client, admin_h):
    set_mode(client, admin_h, "sample")
    return setup_brand(client, admin_h)


# ---- one scan at a time ---------------------------------------------------------------------------------------------


def test_duplicate_scan_start_returns_the_running_scan(client, admin_h, app, sample):
    with app.state.session_factory() as db:
        db.add(Scan(job_id="job_running1", brand_id=sample["id"], scope="all", state="running", started_at=datetime.now(UTC), created_at=datetime.now(UTC)))
        db.commit()
    r = client.post("/api/scan", json={"scope": "all"}, headers=admin_h)
    assert r.status_code == 409
    body = r.json()
    assert body["detail"] == "A scan is already running" and body["job_id"] == "job_running1" and body["scan"]["status"] == "running"
    assert client.get("/api/scan/status", headers=admin_h).json()["running_job_id"] == "job_running1"
    # queued counts as running too
    with app.state.session_factory() as db:
        db.query(Scan).update({"state": "queued"})
        db.commit()
    assert client.post("/api/scan", json={"scope": "social"}, headers=admin_h).status_code == 409
    with app.state.session_factory() as db:
        db.query(Scan).update({"state": "completed"})
        db.commit()
    assert client.post("/api/scan", json={"scope": "social"}, headers=admin_h).status_code == 202


def test_scan_start_needs_a_brand_and_validates_scope(client, admin_h, viewer_h):
    assert client.post("/api/scan", json={"scope": "all"}, headers=admin_h).status_code == 409  # no brand yet
    assert client.post("/api/scan", json={"scope": "everything"}, headers=admin_h).status_code == 422
    assert client.post("/api/scan", json={"scope": "all"}, headers=viewer_h).status_code == 403


def test_scope_social_only_scans_social_platforms(client, admin_h, sample):
    scan = run_scan(client, admin_h, "social")
    assert scan["scope"] == "social"
    assert {i["category"] for i in all_detections(client, admin_h)} == {"social"}
    assert run_scan(client, admin_h, "apps")["scope"] == "apps"
    assert {i["category"] for i in all_detections(client, admin_h)} == {"social", "apps"}


# ---- job dispatch ---------------------------------------------------------------------------------------------------


def test_executor_resolution(settings):
    assert resolve_executor(settings.model_copy(update={"scan_executor": "auto", "redis_url": None})) == "thread"
    assert resolve_executor(settings.model_copy(update={"scan_executor": "auto", "redis_url": SecretStr("redis://h:6379/0")})) == "rq"
    assert resolve_executor(settings.model_copy(update={"scan_executor": "rq", "redis_url": None})) == "thread"  # no Redis -> fallback
    assert resolve_executor(settings.model_copy(update={"scan_executor": "inline"})) == "inline"
    assert resolve_executor(settings.model_copy(update={"scan_executor": "bogus"})) == "thread"


def test_thread_dispatcher_runs_in_background(settings):
    done, main = threading.Event(), threading.get_ident()
    ran: list[int] = []

    def run(scan_id):
        ran.append(threading.get_ident())
        done.set()

    d = JobDispatcher(settings.model_copy(update={"scan_executor": "thread"}), run)
    assert d.enqueue(7) == "thread"
    assert done.wait(5) and ran[0] != main


def test_in_process_thread_mode_finishes_a_real_scan(settings, checker, tmp_path):
    # file-backed SQLite so a worker thread can use its own connection
    from fastapi.testclient import TestClient

    from app.db.base import Base
    from app.db.session import make_engine
    from app.main import create_app

    engine = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    Base.metadata.create_all(engine)
    s = settings.model_copy(update={"scan_executor": "thread"})
    with TestClient(create_app(s, engine=engine, url_checker=checker)) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "sample")
        setup_brand(c, h)
        r = c.post("/api/scan", json={"scope": "all"}, headers=h)
        assert r.status_code == 202
        job = r.json()["job_id"]
        for _ in range(100):
            if c.get(f"/api/jobs/{job}", headers=h).json()["state"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert c.get(f"/api/jobs/{job}", headers=h).json()["status"] == "completed"
        assert len(all_detections(c, h)) > 0


def test_rq_dispatcher_enqueues_when_redis_is_configured(settings, monkeypatch):
    calls = {}

    class FakeRedis:
        @staticmethod
        def from_url(url):
            calls["url"] = url
            return "conn"

    class FakeQueue:
        def __init__(self, name, connection=None):
            calls["queue"], calls["conn"] = name, connection

        def enqueue(self, func, *args, **kw):
            calls["func"], calls["args"], calls["kw"] = func, args, kw

    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=FakeRedis))
    monkeypatch.setitem(sys.modules, "rq", types.SimpleNamespace(Queue=FakeQueue))
    s = settings.model_copy(update={"scan_executor": "auto", "redis_url": SecretStr("redis://localhost:6379/0")})
    d = JobDispatcher(s, lambda i: None)
    assert d.enqueue(42) == "rq"
    assert calls["func"] == "app.worker.run_scan_job" and calls["args"] == (42,) and calls["queue"] == "scans" and calls["kw"]["job_id"] == "scan-42"


def test_rq_failure_falls_back_to_thread(settings, monkeypatch):
    class BrokenRedis:
        @staticmethod
        def from_url(url):
            raise ConnectionError("down")

    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=BrokenRedis))
    monkeypatch.setitem(sys.modules, "rq", types.SimpleNamespace(Queue=object))
    done = threading.Event()
    d = JobDispatcher(settings.model_copy(update={"scan_executor": "rq", "redis_url": SecretStr("redis://x/0")}), lambda i: done.set())
    assert d.enqueue(1) == "thread" and done.wait(5)


def test_worker_module_exposes_the_job_function():
    import app.worker as w

    assert callable(w.run_scan_job) and callable(w.main)


# ---- scheduler ------------------------------------------------------------------------------------------------------


def test_decide_schedule_off_and_due_and_not_due():
    off = decide_schedule(now=UTC_NOW, interval_minutes=0, last_scan_at=None)
    assert off.run is False and off.next_run_at is None
    first = decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=None)
    assert first.run is True and first.next_run_at == UTC_NOW
    not_due = decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=UTC_NOW - timedelta(minutes=10))
    assert not_due.run is False and not_due.next_run_at == UTC_NOW + timedelta(minutes=50)
    due = decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=UTC_NOW - timedelta(minutes=61))
    assert due.run is True
    busy = decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=None, running=True)
    assert busy.run is False and "already running" in busy.reason


def test_decide_schedule_stretches_when_credits_are_scarce():
    # 12 credits per scan, 900 left, 18.5 days to go: 74 affordable scans -> hourly is stretched to every 6 hours...
    hourly = decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=UTC_NOW - timedelta(hours=7), credits_remaining=900, cost_per_scan=12)
    assert hourly.stretched is True and hourly.effective_interval_minutes == 360 and hourly.run is True
    # ...while a daily schedule fits comfortably and is left alone
    plenty = decide_schedule(now=UTC_NOW, interval_minutes=1440, last_scan_at=UTC_NOW - timedelta(days=2), credits_remaining=900, cost_per_scan=12)
    assert plenty.stretched is False and plenty.effective_interval_minutes == 1440 and plenty.run is True
    # 120 left: usable 108 -> 9 scans for 18.5 days -> about every 2 days; hourly becomes much longer
    low = decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=UTC_NOW - timedelta(hours=2), credits_remaining=120, cost_per_scan=12)
    assert low.stretched is True and low.effective_interval_minutes > 60 * 24 and low.run is False
    assert "stretched" in low.reason
    # a daily schedule that already fits is not changed
    daily = decide_schedule(now=UTC_NOW, interval_minutes=1440, last_scan_at=UTC_NOW - timedelta(days=2), credits_remaining=300, cost_per_scan=12)
    assert daily.stretched is False and daily.run is True


def test_decide_schedule_pauses_when_only_the_reserve_is_left():
    d = decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=None, credits_remaining=20, cost_per_scan=12)  # usable 8 < 12
    assert d.run is False and d.skipped_for_credits is True and d.next_run_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert "manual scans still work" in d.reason
    assert decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=None, credits_remaining=0, cost_per_scan=12).skipped_for_credits
    assert decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=None, credits_remaining=24, cost_per_scan=12).run is True  # exactly one spare scan
    assert next_month_start(datetime(2026, 12, 31, 23, tzinfo=UTC)) == datetime(2027, 1, 1, tzinfo=UTC)


def test_no_credit_rule_without_a_credit_limited_source():
    d = decide_schedule(now=UTC_NOW, interval_minutes=60, last_scan_at=UTC_NOW - timedelta(hours=2), credits_remaining=None, cost_per_scan=None)
    assert d.run and not d.stretched


def test_scheduler_tick_starts_scan_as_scheduler_once(settings, checker):
    s = settings.model_copy(update={"scheduler_enabled": True, "scan_interval_minutes": 30})
    with make_client(s, checker) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "sample")
        setup_brand(c, h)
        ctx = c.app_ref.state.scan_ctx
        d = scheduler_tick(ctx)
        assert d.run is True
        scans = c.get("/api/scans", headers=h).json()["items"]
        assert len(scans) == 1 and scans[0]["started_by"] == "scheduler" and scans[0]["status"] == "completed"
        assert scheduler_tick(ctx).run is False  # not due again right away
        with c.app_ref.state.session_factory() as db:
            rows = db.query(AuditLog).filter(AuditLog.action.in_(["scan.started", "scan.finished"])).all()
        assert {r.actor for r in rows} == {"scheduler"}
        st = c.get("/api/scan/status", headers=h).json()
        assert st["schedule"] == "30m" or st["scheduler"]["interval_minutes"] == 30
        assert st["next_scan_at"] and st["scheduler"]["interval_source"] == "env"


def test_scheduler_tick_off_and_without_brand(settings, checker):
    with make_client(settings.model_copy(update={"scheduler_enabled": True, "scan_interval_minutes": 0}), checker) as c:
        h = headers_for(c, *ADMIN)
        ctx = c.app_ref.state.scan_ctx
        assert scheduler_tick(ctx).run is False  # no brand
        setup_brand(c, h)
        d = scheduler_tick(ctx)
        assert d.run is False and "off" in d.reason
        assert c.get("/api/scans", headers=h).json()["total"] == 0
        assert c.get("/api/scan/status", headers=h).json()["next_scan_at"] is None


def test_settings_schedule_is_used_when_env_unset_and_env_overrides(settings, checker):
    with make_client(settings.model_copy(update={"scheduler_enabled": True}), checker) as c:
        h = headers_for(c, *ADMIN)
        setup_brand(c, h)
        assert c.put("/api/settings", json={"monitoring": {"schedule": "15m"}}, headers=h).status_code == 200
        sch = c.get("/api/scan/status", headers=h).json()["scheduler"]
        assert sch["interval_minutes"] == 15 and sch["interval_source"] == "setting" and sch["enabled"] is True
        assert c.put("/api/settings", json={"monitoring": {"schedule": "off"}}, headers=h).status_code == 200
        assert c.get("/api/scan/status", headers=h).json()["scheduler"]["enabled"] is False


def test_credit_aware_status_pauses_scheduled_scans_in_live_mode(settings, checker):
    s = settings.model_copy(update={"scheduler_enabled": True, "scan_interval_minutes": 60, "tavily_api_key": SecretStr("tvly-test-not-real-0123456789"),
                                    "tavily_monthly_budget": 100, "tavily_max_queries_per_scan": 12})
    with make_client(s, checker, [StaticCollector([])]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h)  # with official assets: the scheduler waits for the first one (see test_scheduler_waits_for_the_first_official_asset)
        ok = c.get("/api/scan/status", headers=h).json()["scheduler"]
        assert ok["paused_for_credits"] is False and ok["stretched_for_credits"] is True  # 100 credits: stretched, not paused
        month = c.app_ref.state.collector_runtime.ledger.snapshot()["month"]
        with c.app_ref.state.session_factory() as db:
            db.add(ApiUsage(provider="tavily", month=month, credits=95))
            db.commit()
        paused = c.get("/api/scan/status", headers=h).json()["scheduler"]
        assert paused["paused_for_credits"] is True and "manual scans still work" in paused["reason"]
        assert scheduler_tick(c.app_ref.state.scan_ctx).skipped_for_credits is True
        assert c.get("/api/scans", headers=h).json()["total"] == 0
        assert run_scan(c, h)["status"] == "completed"  # a manual scan still works


def test_scheduler_waits_for_the_first_official_asset(settings, checker):
    """D5: a brand with no official account or app is not scanned automatically (no credits spent before the allowlist can work)."""
    s = settings.model_copy(update={"scheduler_enabled": True, "scan_interval_minutes": 60})
    with make_client(s, checker, [StaticCollector([])]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "sample")
        b = setup_brand(c, h, accounts=(), apps=())
        sch = c.get("/api/scan/status", headers=h).json()["scheduler"]
        assert sch["waiting_for_official_assets"] is True and sch["next_scan_at"] is None and "first official account or app" in sch["reason"]
        d = scheduler_tick(c.app_ref.state.scan_ctx)
        assert d.run is False and "first official" in d.reason
        assert c.get("/api/scans", headers=h).json()["total"] == 0
        # the first official asset releases the scheduler: the very next tick starts the first scan
        assert c.post(f"/api/brands/{b['id']}/accounts", json={"platform": "instagram", "handle": "acmebank"}, headers=h).status_code == 201
        sch = c.get("/api/scan/status", headers=h).json()["scheduler"]
        assert sch["waiting_for_official_assets"] is False and sch["next_scan_at"]
        assert scheduler_tick(c.app_ref.state.scan_ctx).run is True
        assert c.get("/api/scans", headers=h).json()["total"] == 1
        # manual scans were never blocked
        assert run_scan(c, h) is not None


def test_scheduler_loop_is_not_started_in_tests(settings, checker):
    with make_client(settings.model_copy(update={"scheduler_enabled": True}), checker) as c:
        assert c.app_ref.state.scheduler_loop is None  # APP_ENV=test never starts the loop


# ---- lists: filters, sort, pagination, tab counts -------------------------------------------------------------------


def test_filters_sort_pagination_and_tab_counts(client, admin_h, sample):
    run_scan(client, admin_h)
    r = client.get("/api/detections", params={"limit": 200}, headers=admin_h).json()
    items, total = r["items"], r["total"]
    assert total == len(items) > 4 and r["data_mode"] == "sample" and r["is_sample"] is True and r["includes_sample"] is True
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True)  # default sort: score desc
    asc = [i["score"] for i in all_detections(client, admin_h, sort="score_asc")]
    assert asc == sorted(asc)
    # tab counts add up
    counts = r["counts"]
    assert counts["all"] == total and sum(counts["by_type"].values()) == total and sum(counts["by_platform"].values()) == total
    assert sum(counts["by_status"].values()) == total and sum(counts["by_band"].values()) == total
    # type / category / platform filters
    ftype = next(t for t, n in counts["by_type"].items() if n)
    got = all_detections(client, admin_h, type=ftype)
    assert got and {i["type"] for i in got} == {ftype} and len(got) == counts["by_type"][ftype]
    assert {i["category"] for i in all_detections(client, admin_h, type="social")} == {"social"}
    assert all(i["category"] == "apps" for i in all_detections(client, admin_h, type="app"))
    assert client.get("/api/detections", params={"type": "bogus"}, headers=admin_h).status_code == 422
    plat = next(iter(counts["by_platform"]))
    assert {i["platform"] for i in all_detections(client, admin_h, platform=plat)} == {plat}
    # the type filter does not change the by_type tab counts
    assert client.get("/api/detections", params={"type": ftype}, headers=admin_h).json()["counts"]["by_type"] == counts["by_type"]
    # band / status / min_score / q / source
    band = items[0]["risk_band"]
    assert {i["risk_band"] for i in all_detections(client, admin_h, band=band)} == {band}
    assert all(i["score"] >= items[0]["score"] for i in all_detections(client, admin_h, min_score=items[0]["score"]))
    first = items[0]
    client.post(f"/api/detections/{first['id']}/confirm", headers=admin_h)
    assert [i["id"] for i in all_detections(client, admin_h, status="confirmed")] == [first["id"]]
    assert {i["status"] for i in all_detections(client, admin_h, status=["new", "confirmed"])} == {"new", "confirmed"}
    assert client.get("/api/detections", params={"status": "bogus"}, headers=admin_h).status_code == 422
    handle = (first["handle"] or first["package_id"] or first["name"]).lstrip("@")
    hits = all_detections(client, admin_h, q=handle[:6])
    assert first["id"] in [i["id"] for i in hits]
    assert all_detections(client, admin_h, q="zzzz-no-such-thing") == []
    assert all_detections(client, admin_h, q="%") == []  # LIKE wildcards are escaped, not interpreted
    assert all_detections(client, admin_h, source="live") == []  # none are live
    assert len(all_detections(client, admin_h, source="all")) == total
    # pagination
    page1 = client.get("/api/detections", params={"limit": 2, "offset": 0}, headers=admin_h).json()
    page2 = client.get("/api/detections", params={"limit": 2, "offset": 2}, headers=admin_h).json()
    assert page1["total"] == page2["total"] == total and len(page1["items"]) == 2
    assert [i["id"] for i in page1["items"]] + [i["id"] for i in page2["items"]] == [i["id"] for i in items][:4]
    assert client.get("/api/detections", params={"limit": 500}, headers=admin_h).status_code == 422
    # date range and found sort
    assert len(all_detections(client, admin_h, sort="found_desc")) == total
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert all_detections(client, admin_h, **{"from": future}) == []


def test_data_mode_honesty_in_lists_and_dashboard(client, admin_h, sample):
    run_scan(client, admin_h)
    assert len(all_detections(client, admin_h)) > 0
    set_mode(client, admin_h, "live")  # sample rows must not be presented as live results
    r = client.get("/api/detections", headers=admin_h).json()
    assert r["items"] == [] and r["data_mode"] == "live" and r["is_sample"] is False
    d = client.get("/api/dashboard", headers=admin_h).json()
    assert d["is_sample"] is False and d["data_mode"] == "live" and d["kpis"]["total_detections"]["value"] == 0 and d["recent"] == []
    assert client.get("/api/detections", params={"source": "sample"}, headers=admin_h).json()["total"] > 0  # explicit opt-in only
    set_mode(client, admin_h, "sample")
    assert client.get("/api/dashboard", headers=admin_h).json()["is_sample"] is True


def test_dashboard_numbers_come_from_data(client, admin_h, sample):
    empty = client.get("/api/dashboard", headers=admin_h).json()
    assert empty["kpis"]["total_detections"] == {"value": 0, "delta_week": 0, "trend": [0] * 7} and empty["top_detection"] is None
    assert empty["kpis"]["official_assets"]["value"] == 3 and empty["last_scan_at"] is None and empty["sources_total"] == 8
    scan = run_scan(client, admin_h)
    d = client.get("/api/dashboard", headers=admin_h).json()
    items = all_detections(client, admin_h)
    n = len(items)
    assert d["kpis"]["total_detections"]["value"] == n == d["total_open"]
    assert d["kpis"]["total_detections"]["delta_week"] == n and d["kpis"]["total_detections"]["trend"][-1] == n and len(d["kpis"]["total_detections"]["trend"]) == 7
    social_n = sum(i["category"] == "social" for i in items)
    apps_n = sum(i["category"] == "apps" for i in items)
    web_n = sum(i["category"] == "web" for i in items)
    assert web_n > 0 and social_n + apps_n + web_n == n
    assert d["kpis"]["social_impersonations"]["value"] == social_n and d["kpis"]["suspicious_apps"]["value"] == apps_n
    assert d["kpis"]["web_threats"]["value"] == web_n and d["counts"]["by_category"] == {"social": social_n, "apps": apps_n, "web": web_n}
    assert sum(t["percent"] for t in d["by_type"]) == 100 and sum(t["count"] for t in d["by_type"]) == n
    assert sum(p["count"] for p in d["by_platform"]) == n
    assert d["counts"]["by_status"]["new"] == n and sum(d["counts"]["by_band"].values()) == n
    assert d["counts"]["cleared"] > 0
    assert len(d["recent"]) == min(6, n) and len(d["needs_review"]) == min(5, n)
    assert [x["score"] for x in d["needs_review"]] == sorted((x["score"] for x in d["needs_review"]), reverse=True)
    top = max(items, key=lambda i: i["score"])
    assert d["top_detection"]["id"] == top["id"] and d["why_flagged"]["score"] == top["score"] and d["why_flagged"]["reasons"]
    assert d["last_scan_at"] and d["last_scan"]["job_id"] == scan["job_id"] and d["sources_total"] == 8 and d["is_sample"] is True
    assert d["credits"] is None  # sample mode: no credit meter
    client.post(f"/api/detections/{top['id']}/confirm", headers=admin_h)
    d2 = client.get("/api/dashboard", headers=admin_h).json()
    assert d2["counts"]["by_status"]["confirmed"] == 1 and d2["counts"]["by_status"]["new"] == n - 1 and top["id"] not in [x["id"] for x in d2["needs_review"]]
    client.post(f"/api/detections/{items[-1]['id']}/dismiss", headers=admin_h)
    d3 = client.get("/api/dashboard", headers=admin_h).json()
    assert d3["kpis"]["total_detections"]["value"] == n - 1 and d3["counts"]["by_status"]["dismissed"] == 1


def test_dashboard_shows_credit_meter_and_next_scan_in_live_mode(settings, checker):
    s = settings.model_copy(update={"scheduler_enabled": True, "scan_interval_minutes": 60})
    with make_client(s, checker, [StaticCollector([social("instagram", "acrnebank")])]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h)
        d = c.get("/api/dashboard", headers=h).json()
        assert d["credits"]["budget"] == 900 and d["next_scan_at"] and d["schedule"]["enabled"] is True
        assert d["is_sample"] is False


# ---- auth matrix ----------------------------------------------------------------------------------------------------

READ_ENDPOINTS = [
    "/api/scans", "/api/scans/latest", "/api/scan/status", "/api/detections", "/api/detections/excluded", "/api/detections/false-positives",
    "/api/dashboard", "/api/lookalike/test-cases", "/api/settings", "/api/reports", "/api/reports/preview", "/api/reports/preview.html",
    "/api/evaluation", "/api/sources", "/api/settings/data-mode",
]
WRITER_ENDPOINTS = [
    ("POST", "/api/scan", {"scope": "all"}),
    ("POST", "/api/detections/1/status", {"status": "confirmed"}),
    ("POST", "/api/detections/1/confirm", {}),
    ("POST", "/api/detections/1/keep-reviewing", {}),
    ("POST", "/api/detections/1/dismiss", {}),
    ("POST", "/api/detections/1/mark-official", {}),
    ("POST", "/api/reports", {"format": "csv"}),
    ("POST", "/api/reports/1/approve", None),
    ("POST", "/api/lookalike/test-cases/run", None),
    ("POST", "/api/evaluation/run", None),
]
ADMIN_ENDPOINTS = [
    ("PUT", "/api/settings", {"monitoring": {"sensitivity": 80}}),
    ("POST", "/api/lookalike/test-cases", {"name": "Acme Pay", "expected": "flagged"}),
    ("DELETE", "/api/lookalike/test-cases/1", None),
]


def _call(client, method, path, body, headers=None):
    return client.request(method, path, json=body, headers=headers or {})


def test_every_endpoint_requires_authentication(client, sample):
    for path in READ_ENDPOINTS:
        assert client.get(path).status_code == 401, path
    for path in ("/api/jobs/x", "/api/scans/1", "/api/detections/1", "/api/detections/1/evidence", "/api/detections/1/evidence/screenshot",
                 "/api/reports/1", "/api/reports/1/preview", "/api/reports/1/download", "/api/reports/1/export.csv", "/api/reports/1/export.html"):
        assert client.get(path).status_code == 401, path
    for method, path, body in [*WRITER_ENDPOINTS, *ADMIN_ENDPOINTS, ("POST", "/api/lookalike/test", {"name": "x"}),
                               ("POST", "/api/settings/sensitivity-preview", {"sensitivity": 70})]:
        assert _call(client, method, path, body).status_code == 401, (method, path)


def test_viewer_is_read_only(client, admin_h, viewer_h, sample):
    run_scan(client, admin_h)
    for path in READ_ENDPOINTS:
        if path == "/api/audit":
            continue
        assert client.get(path, headers=viewer_h).status_code == 200, path
    # pure computations are allowed for viewers
    assert client.post("/api/lookalike/test", json={"name": "acrnebank"}, headers=viewer_h).status_code == 200
    assert client.post("/api/settings/sensitivity-preview", json={"sensitivity": 70}, headers=viewer_h).status_code == 200
    for method, path, body in [*WRITER_ENDPOINTS, *ADMIN_ENDPOINTS]:
        assert _call(client, method, path, body, viewer_h).status_code == 403, (method, path)


def test_analyst_can_act_but_not_change_settings(client, admin_h, analyst_h, sample):
    run_scan(client, admin_h)
    det = all_detections(client, admin_h)[0]
    assert client.post(f"/api/detections/{det['id']}/confirm", headers=analyst_h).status_code == 200
    assert client.post("/api/scan", json={"scope": "all"}, headers=analyst_h).status_code == 202
    assert client.post("/api/reports", json={"format": "csv"}, headers=analyst_h).status_code == 201
    assert client.post("/api/lookalike/test-cases/run", headers=analyst_h).status_code == 200
    for method, path, body in ADMIN_ENDPOINTS:
        assert _call(client, method, path, body, analyst_h).status_code == 403, (method, path)
    # webhook is only shown to admins
    client.put("/api/settings", json={"notifications": {"webhook_url": "https://hooks.example.com/secret-token"}}, headers=admin_h)
    assert client.get("/api/settings", headers=analyst_h).json()["notifications"]["webhook_url"] == ""
    assert client.get("/api/settings", headers=admin_h).json()["notifications"]["webhook_url"].endswith("secret-token")


# ---- audit ----------------------------------------------------------------------------------------------------------


def test_every_mutation_writes_an_audit_row(client, admin_h, app, sample):
    def actions():
        with app.state.session_factory() as db:
            return [a.action for a in db.query(AuditLog).order_by(AuditLog.id)]

    def count(action):
        return actions().count(action)

    run_scan(client, admin_h)
    assert count("scan.started") == 1 and count("scan.finished") == 1
    items = all_detections(client, admin_h)
    for path, action in (("confirm", "detection.status_changed"), ("keep-reviewing", "detection.status_changed"), ("dismiss", "detection.status_changed")):
        before = count(action)
        assert client.post(f"/api/detections/{items[0]['id']}/{path}", headers=admin_h).status_code == 200
        assert count(action) == before + 1
    social_item = next(i for i in items if i["category"] == "social" and i["id"] != items[0]["id"])
    assert client.post(f"/api/detections/{social_item['id']}/mark-official", json={}, headers=admin_h).status_code == 200
    assert count("detection.marked_official") == 1 and "account.added" in actions()
    client.post(f"/api/detections/{items[0]['id']}/confirm", headers=admin_h)
    rep = client.post("/api/reports", json={"format": "html"}, headers=admin_h).json()
    client.post(f"/api/reports/{rep['id']}/approve", headers=admin_h)
    assert count("report.generated") == 1 and count("report.approved") == 1
    client.put("/api/settings", json={"monitoring": {"sensitivity": 80, "sources": {"telegram": False}}, "data_mode": "live"}, headers=admin_h)
    assert count("settings.updated") == 1 and count("source.monitor_toggled") == 1 and count("settings.data_mode_changed") >= 2
    client.put("/api/settings", json={"monitoring": {"sensitivity": 80}}, headers=admin_h)  # no change -> no audit row
    assert count("settings.updated") == 1
    case = client.post("/api/lookalike/test-cases", json={"name": "Acme Pay", "expected": "flagged"}, headers=admin_h).json()
    client.delete(f"/api/lookalike/test-cases/{case['id']}", headers=admin_h)
    client.post("/api/lookalike/test-cases/run", headers=admin_h)
    client.post("/api/evaluation/run", headers=admin_h)
    for a in ("lookalike.case_added", "lookalike.case_removed", "lookalike.cases_run", "evaluation.run"):
        assert count(a) == 1, a
    with app.state.session_factory() as db:
        assert all(r.actor for r in db.query(AuditLog).all())

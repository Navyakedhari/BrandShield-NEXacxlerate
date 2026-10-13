"""Scan pipeline behaviours with controlled collectors: partial scans, hard negatives, logo fetch (SSRF), prefilter, LLM, screenshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.engine.llm import LLMIntentResult
from app.core.pipeline.image_fetch import FetchError, SafeFetcher
from app.models import Scan
from tests.scan_helpers import (
    FailingCollector,
    SampleCollector,
    StaticCollector,
    all_detections,
    app_candidate,
    make_client,
    png_bytes,
    run_scan,
    set_mode,
    setup_brand,
    social,
)

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def test_failed_source_gives_partial_scan_and_keeps_other_results(settings, checker):
    with make_client(settings, checker, [SampleCollector(), FailingCollector()]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "sample")
        setup_brand(c, h)
        scan = run_scan(c, h)
        assert scan["status"] == "partial" and scan["state"] == "done"  # not failed, not error
        assert scan["counts"]["detections_new"] > 0  # the other source's results were saved
        assert any("failing: unreachable" in e for e in scan["errors"])
        by = {r["collector"]: r["status"] for r in scan["source_report"]}
        assert by == {"sample": "online", "failing": "unavailable"}
        assert len(all_detections(c, h)) == scan["counts"]["detections_new"]
        assert c.get(f"/api/jobs/{scan['job_id']}", headers=h).json()["message"].startswith("Scan finished with some sources unavailable")


def test_every_source_unavailable_is_failed_with_clear_message(settings, checker):
    with make_client(settings, checker, [FailingCollector()]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        setup_brand(c, h)
        scan = run_scan(c, h)
        assert scan["status"] == "failed" and scan["state"] == "error"
        assert "nothing could be checked" in scan["message"]
        assert scan["counts"]["unique_candidates"] == 0


def test_hard_negatives_are_not_flagged_and_land_in_cleared_list(settings, checker):
    cands = [
        social("facebook", "acmebakery", "Acme Bakery", bio="Fresh bread daily.", followers=900, posts=300),
        social("instagram", "acmehotels", "Acme Hotels", bio="Book your stay.", followers=5000, posts=800),
        social("x", "acmeweather", "Acme Weather", bio="Forecasts.", followers=1200, posts=2000),
        app_candidate("google_play", "com.acme.weather", "Acme Weather", publisher="Acme Corp"),
        social("instagram", "acrnebank", "Acrnebank Support", bio="Official support. Share your KYC details in DM."),
    ]
    with make_client(settings, checker, [StaticCollector(cands)]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        scan = run_scan(c, h)
        assert scan["status"] == "completed"
        names = {i["name"] for i in all_detections(c, h)}
        assert names == {"Acrnebank Support"}  # only the real look-alike
        cleared = {i["name"] for i in c.get("/api/detections/false-positives", headers=h).json()["items"]}
        assert {"Acme Bakery", "Acme Hotels", "Acme Weather"} <= cleared
        assert scan["is_sample"] is False
        assert all(i["source"] == "live" for i in all_detections(c, h))  # never invented as sample


def test_prefilter_skips_unrelated_candidates_and_can_be_disabled(settings, checker):
    cands = [social("instagram", "greenvalleyfarms", "Green Valley Farms", bio="Organic veg."), social("x", "acrnebank", "Acrnebank", bio="x")]
    with make_client(settings, checker, [StaticCollector(cands)]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        scan = run_scan(c, h)
        assert scan["counts"]["prefiltered"] == 1 and scan["counts"]["evaluated"] == 1
    s2 = settings.model_copy(update={"scan_prefilter": False})
    with make_client(s2, checker, [StaticCollector(cands)]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        assert run_scan(c, h)["counts"]["prefiltered"] == 0


def test_dedupe_same_handle_and_same_canonical_url(settings, checker):
    a = social("instagram", "Acrnebank", "Acrnebank")
    b = social("instagram", "acrnebank", "Acrnebank Official", bio="more detail")  # same handle, different case
    d = social("x", "acrnebank2", "Acrnebank 2")
    d2 = social("x", "other_handle", "Other", )
    d2.profile_url = d.profile_url  # same canonical URL, different handle
    with make_client(settings, checker, [StaticCollector([a, b, d, d2])]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        scan = run_scan(c, h)
        # same platform + handle (any case) is merged by the collector orchestrator; the pipeline merges the same canonical URL
        assert scan["counts"]["candidates_found"] == 3
        assert scan["counts"]["duplicates_removed"] == 1 and scan["counts"]["unique_candidates"] == 2


def test_name_and_handle_are_both_matched(settings, checker):
    # display name is unrelated but the handle is a look-alike; and the reverse
    cands = [social("instagram", "acrnebank", "Daily Deals"), social("x", "dailydeals99", "Acme Bnak")]
    with make_client(settings, checker, [StaticCollector(cands)]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        run_scan(c, h)
        assert {i["platform"] for i in all_detections(c, h)} == {"instagram", "x"}


def test_allowlist_is_exact_not_by_name(settings, checker):
    cands = [
        social("instagram", "acmebank", "Acme Bank"),  # exact official handle -> excluded
        social("instagram", "acme_bank_real", "Acme Bank"),  # same NAME, other handle -> NOT excluded
        app_candidate("google_play", "com.acmebank.mobile", "Acme Bank Mobile", publisher="Acme Bank Ltd"),  # official package
        app_candidate("third_party_apk", "com.acmebank.mobile", "Acme Bank Mobile", publisher="Mirror"),  # mirror of official package -> NOT official
    ]
    with make_client(settings, checker, [StaticCollector(cands)]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h)
        scan = run_scan(c, h)
        assert scan["counts"]["official_excluded"] == 2
        keys = {(i["platform"], i["handle"] or i["package_id"]) for i in all_detections(c, h)}
        assert ("instagram", "@acme_bank_real") in keys and ("third_party_apk", "com.acmebank.mobile") in keys
        assert ("instagram", "@acmebank") not in keys and ("google_play", "com.acmebank.mobile") not in keys


def test_adding_official_account_closes_existing_detection(settings, checker):
    with make_client(settings, checker, [StaticCollector([social("instagram", "acme_bank_care", "Acme Bank Care")])]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        b = setup_brand(c, h, accounts=(), apps=())
        run_scan(c, h)
        det = all_detections(c, h)[0]
        assert c.post(f"/api/brands/{b['id']}/accounts", json={"platform": "instagram", "handle": "acme_bank_care"}, headers=h).status_code == 201
        assert all_detections(c, h) == []
        assert c.get(f"/api/detections/{det['id']}", headers=h).json()["status"] == "marked_official"


# ---- logo fetch: SSRF ----------------------------------------------------------------------------------------------


def _fetcher(handler, **kw):
    transport = httpx.MockTransport(handler)
    return SafeFetcher(resolver=PUBLIC, client_factory=lambda: httpx.Client(transport=transport), **kw)


def test_fetcher_refuses_private_hosts_without_any_request():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200)

    f = _fetcher(handler)
    for url in ("http://10.0.0.5/logo.png", "http://127.0.0.1/logo.png", "http://169.254.169.254/latest/meta-data", "http://[::1]/x.png",
                "http://localhost/x.png", "ftp://example.com/x.png", "http://user:pw@example.com/x.png", "https://example.com:8443/x.png"):
        with pytest.raises(FetchError, match="Refused"):
            f.fetch_bytes(url)
    assert calls == []
    private_dns = SafeFetcher(resolver=lambda h: ["10.1.2.3"], client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(FetchError, match="Refused"):
        private_dns.fetch_bytes("https://evil.example.com/logo.png")
    assert calls == []


def test_fetcher_revalidates_every_redirect_hop():
    def handler(req):
        if req.headers["host"] == "good.example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, content=b"x")

    with pytest.raises(FetchError, match="Refused"):
        _fetcher(handler).fetch_bytes("https://good.example.com/a.png")

    def loop(req):
        return httpx.Response(302, headers={"location": "https://good.example.com/b.png"})

    with pytest.raises(FetchError, match="redirects"):
        _fetcher(loop, max_redirects=2).fetch_bytes("https://good.example.com/a.png")


def test_fetcher_content_type_size_and_magic_checks():
    png = png_bytes()
    ok = _fetcher(lambda r: httpx.Response(200, content=png, headers={"content-type": "image/png"}))
    assert ok.fetch_image("https://good.example.com/a.png").size == (300, 300)
    with pytest.raises(FetchError, match="Not an image"):
        _fetcher(lambda r: httpx.Response(200, content=png, headers={"content-type": "text/html"})).fetch_bytes("https://good.example.com/a")
    with pytest.raises(FetchError, match="too large"):
        _fetcher(lambda r: httpx.Response(200, content=png, headers={"content-type": "image/png"}), max_bytes=100).fetch_bytes("https://good.example.com/a")
    with pytest.raises(FetchError, match="not a supported image"):
        _fetcher(lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "image/png"})).fetch_bytes("https://good.example.com/a")
    with pytest.raises(FetchError, match="HTTP 404"):
        _fetcher(lambda r: httpx.Response(404, headers={"content-type": "image/png"})).fetch_bytes("https://good.example.com/a")
    with pytest.raises(FetchError, match="Timed out"):
        _fetcher(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow"))).fetch_bytes("https://good.example.com/a")


def _upload_logo(c, h, brand_id, data):
    r = c.post(f"/api/brands/{brand_id}/logo", files={"file": ("logo.png", data, "image/png")}, headers=h)
    assert r.status_code == 200, r.text


def test_scan_logo_on_private_ip_is_refused_and_candidate_survives(settings, checker):
    hits = []
    fetcher = _fetcher(lambda r: hits.append(r) or httpx.Response(200))
    bad = app_candidate("app_store", "com.acme.fakebank", "Acme Bank Mobile", publisher="Evil Co")
    bad.icon_url = "http://10.0.0.5/icon.png"
    with make_client(settings, checker, [StaticCollector([bad])], image_fetcher=fetcher) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        b = setup_brand(c, h, accounts=(), apps=())
        _upload_logo(c, h, b["id"], png_bytes())
        scan = run_scan(c, h)
        assert hits == [] and scan["counts"]["logo_unavailable"] == 1 and scan["counts"]["logo_compared"] == 0
        assert scan["status"] == "completed"
        det = all_detections(c, h)[0]  # the candidate is still flagged on its name
        d = c.get(f"/api/detections/{det['id']}", headers=h).json()
        assert d["logo"]["status"] == "unavailable" and "Refused" in d["logo"]["reason"] and "Logo unavailable" in d["logo"]["reason"]


def test_scan_logo_similarity_adds_points_when_fetch_works(settings, checker):
    logo = png_bytes()
    fetcher = _fetcher(lambda r: httpx.Response(200, content=logo, headers={"content-type": "image/png"}))
    copy = social("instagram", "acrnebank", "Acrnebank", bio="hello")
    copy.icon_url = "https://cdn.example.com/pic.png"
    with make_client(settings, checker, [StaticCollector([copy])], image_fetcher=fetcher) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        b = setup_brand(c, h, accounts=(), apps=())
        _upload_logo(c, h, b["id"], logo)
        scan = run_scan(c, h)
        assert scan["counts"]["logo_compared"] == 1
        d = c.get(f"/api/detections/{all_detections(c, h)[0]['id']}", headers=h).json()
        assert d["logo"]["status"] == "compared" and d["logo"]["similarity"] >= 80
        assert any(r["signal"] == "logo_similarity" and r["points"] > 0 for r in d["reasons"])
        assert d["suspect"]["logo_match_percent"] >= 80 and "Logo" in " ".join(d["tags"])


def test_logo_unavailable_when_no_official_logo_or_no_url(settings, checker):
    cands = [social("instagram", "acrnebank", "Acrnebank", bio="hello")]
    with make_client(settings, checker, [StaticCollector(cands)]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        scan = run_scan(c, h)
        assert scan["counts"]["logo_unavailable"] == 1
        d = c.get(f"/api/detections/{all_detections(c, h)[0]['id']}", headers=h).json()
        assert "no official logo" in d["logo"]["reason"]


# ---- optional LLM / screenshot hook ---------------------------------------------------------------------------------


class FakeLLM:
    def __init__(self):
        self.calls = []

    def assess(self, text):
        self.calls.append(text)
        return LLMIntentResult(0.9, ["kyc"], "asks for KYC")


def test_llm_only_when_enabled_and_key_present(settings, checker):
    cands = [social("instagram", "acrnebank", "Acrnebank", bio="Please share your account details here to continue today")]
    from tests.conftest import ADMIN, headers_for

    # setting off (the default): nothing is called, no note
    with make_client(settings, checker, [StaticCollector(cands)]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        scan = run_scan(c, h)
        assert scan["counts"]["llm_checked"] == 0 and scan["errors"] == []
    # setting on but key missing: skipped with a note
    on = settings.model_copy(update={"scam_llm_enabled": True})
    with make_client(on, checker, [StaticCollector(cands)]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        scan = run_scan(c, h)
        assert scan["counts"]["llm_checked"] == 0 and any("is not usable" in e for e in scan["errors"])
    # setting on + fake LLM: called, adds to the bio signal
    fake = FakeLLM()
    with make_client(on, checker, [StaticCollector(cands)]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        c.app_ref.state.scan_ctx.llm = fake
        scan = run_scan(c, h)
        assert scan["counts"]["llm_checked"] == 1 and len(fake.calls) == 1
        d = c.get(f"/api/detections/{all_detections(c, h)[0]['id']}", headers=h).json()
        assert d["extras"] == {} and any("llm_intent" in r["detail"] for r in d["reasons"])


def test_llm_never_sees_sample_data(settings, checker):
    on = settings.model_copy(update={"scam_llm_enabled": True})
    fake = FakeLLM()
    with make_client(on, checker, [SampleCollector()]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "sample")
        setup_brand(c, h)
        c.app_ref.state.scan_ctx.llm = fake
        run_scan(c, h)
        assert fake.calls == []


class FakeShots:
    def __init__(self, png):
        self.png, self.urls = png, []

    def capture(self, url):
        self.urls.append(url)
        return self.png


def test_screenshot_hook_is_off_by_default_and_stores_hash_when_on(settings, checker):
    assert settings.screenshots_enabled is False
    cands = [social("instagram", "acrnebank", "Acrnebank", bio="hello")]
    shots = FakeShots(png_bytes())
    with make_client(settings, checker, [StaticCollector(cands)], screenshot_provider=shots) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())
        run_scan(c, h)
        det = all_detections(c, h)[0]
        d = c.get(f"/api/detections/{det['id']}", headers=h).json()
        assert d["evidence"]["screenshot_url"] and d["evidence"]["screenshot_sha256"] and shots.urls == [cands[0].profile_url]
        r = c.get(d["evidence"]["screenshot_url"], headers=h)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == shots.png


def test_stale_scan_is_failed_so_a_new_one_can_start(settings, checker):
    with make_client(settings, checker, [StaticCollector([])]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        setup_brand(c, h, accounts=(), apps=())
        with c.app_ref.state.session_factory() as db:
            db.add(Scan(job_id="job_old", brand_id=1, scope="all", state="running", created_at=datetime.now(UTC) - timedelta(hours=3),
                        started_at=datetime.now(UTC) - timedelta(hours=3)))
            db.commit()
        assert run_scan(c, h)["status"] == "completed"
        assert c.get("/api/jobs/job_old", headers=h).json()["status"] == "failed"


def test_scan_crash_is_recorded_as_failed_without_secrets(settings, checker):
    with make_client(settings, checker, [StaticCollector([social("instagram", "acrnebank")])]) as c:
        from tests.conftest import ADMIN, headers_for

        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h, accounts=(), apps=())

        def explode(*_a, **_k):
            raise RuntimeError("token=supersecretvalue1234")

        import app.core.pipeline.scan_pipeline as sp

        orig = sp.evaluate
        sp.evaluate = explode
        try:
            scan = run_scan(c, h)
        finally:
            sp.evaluate = orig
        assert scan["status"] == "failed"
        assert "supersecretvalue1234" not in str(scan) and scan["errors"]
        assert "RuntimeError" in scan["errors"][0]

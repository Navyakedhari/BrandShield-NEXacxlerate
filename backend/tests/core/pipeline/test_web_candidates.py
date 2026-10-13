"""The `web` candidate category end to end: scan pipeline, Safe Browsing wiring (mocked HTTP), API filters, counts, dashboard."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config.config import Settings
from app.core.collectors.sample import SampleCollector
from app.core.pipeline.image_fetch import SafeFetcher
from tests.conftest import ADMIN, headers_for
from tests.scan_helpers import StaticCollector, all_detections, make_client, run_scan, set_mode, setup_brand, social, web

KEY = "AIza-test-key-not-real-0123456789"
BAD = "https://acmebank.com.evil.test/login"  # brand in the subdomain
TYPO = "https://acmebnk.net/"
UNRELATED = "https://weather-today.example.org/"
OFFICIAL = "https://www.acmebank.com/accounts"


class SB:
    """Mock Safe Browsing endpoint. `matches` = URLs to report as threats."""

    def __init__(self, matches=(), status=200, headers=None) -> None:
        self.matches, self.status, self.headers, self.requests = set(matches), status, headers or {}, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.url.host != "safebrowsing.googleapis.com":
            raise AssertionError(f"unexpected outbound request to {req.url.host}: candidate URLs must never be visited")
        if self.status != 200:
            return httpx.Response(self.status, headers=self.headers, json={"error": {"status": "X", "message": "x"}})
        sent = [e["url"] for e in json.loads(req.content)["threatInfo"]["threatEntries"]]
        hits = [u for u in sent if u in self.matches]
        return httpx.Response(200, json={"matches": [{"threatType": "SOCIAL_ENGINEERING", "threat": {"url": u}} for u in hits]} if hits else {})

    @property
    def sent(self) -> list[str]:
        return [e["url"] for r in self.requests for e in json.loads(r.content)["threatInfo"]["threatEntries"]]


def live_settings(settings: Settings, key: str | None = KEY) -> Settings:
    return Settings(_env_file=None, data_dir=settings.data_dir, database_url="sqlite://", google_safe_browsing_api_key=key)  # type: ignore[call-arg]


def candidates():
    return [web(BAD), web(TYPO), web(UNRELATED), web(OFFICIAL), social("instagram", "acmebnk", "Acme Bnk")]


@pytest.fixture(autouse=True)
def never_open_candidate_urls(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a candidate URL was fetched")

    monkeypatch.setattr(SafeFetcher, "fetch_image", boom)
    monkeypatch.setattr(SafeFetcher, "fetch_bytes", boom)


def run(settings, checker, sb: SB, cands=None, key: str | None = KEY, mode="live"):
    ctx = make_client(live_settings(settings, key), checker, [StaticCollector(cands if cands is not None else candidates())], transport=httpx.MockTransport(sb))
    c = ctx.__enter__()
    h = headers_for(c, *ADMIN)
    set_mode(c, h, mode)
    setup_brand(c, h)
    return ctx, c, h, run_scan(c, h)


def web_items(c, h, **params):
    return all_detections(c, h, category="web", **params)


def by_host(items):
    return {i["handle"]: i for i in items}


# ---- pipeline ------------------------------------------------------------------------------------------


def test_web_candidates_become_detections_and_are_never_visited(settings, checker):
    sb = SB()
    ctx, c, h, scan = run(settings, checker, sb)
    try:
        items = by_host(web_items(c, h))
        assert set(items) == {"acmebank.com.evil.test", "acmebnk.net"}  # unrelated and official are not detections
        bad = items["acmebank.com.evil.test"]
        assert bad["category"] == "web" and bad["type"] == "lookalike_domain" and bad["platform"] == "web" and bad["status"] == "new"
        assert bad["severity"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL") and bad["risk_band"] in ("high", "medium", "low")
        counts = scan["counts"]
        assert counts["web_candidates"] == 4 and counts["official_excluded"] == 1 and scan["status"] in ("completed", "partial")
        assert counts["reputation_checked"] == 2 and counts["reputation_clean"] == 2
        # only the two brand-related URLs were sent to Google; the unrelated one and the official one were not
        assert sorted(sb.sent) == sorted([BAD, TYPO]) and len(sb.requests) == 1
        assert KEY not in str(sb.requests[0].url) and sb.requests[0].headers["x-goog-api-key"] == KEY
    finally:
        ctx.__exit__(None, None, None)


def test_web_detection_detail_has_signals_provenance_and_domain_components(settings, checker):
    ctx, c, h, _ = run(settings, checker, SB(matches=[BAD]))
    try:
        d = c.get(f"/api/detections/{by_host(web_items(c, h))['acmebank.com.evil.test']['id']}", headers=h).json()
        assert d["category"] == "web" and d["algorithm_version"] == "2.0" and d["weights"] == "weights_v2_web" and d["scored_at"]
        sig = {s["id"]: s for s in d["signals"]}
        assert sig["url_reputation"]["status"] == "present" and sig["url_reputation"]["provenance"] == "real_api" and sig["url_reputation"]["checked_at"]
        assert sig["deceptive_subdomain"]["status"] == "present" and sig["deceptive_subdomain"]["provenance"] == "local_algorithm"
        assert d["reputation"]["verdict"] == "match" and d["reputation"]["threat_types"] == ["SOCIAL_ENGINEERING"]
        assert d["suspect"]["registrable_domain"] == "evil.test" and d["domain"]["components"]["brand_in_subdomain"] is True
        assert d["official"]["domains"] == ["acmebank.com"]
        assert d["score"] == sum(r["points"] for r in d["reasons"]) or d["capped"]
        assert any(r["signal"] == "url_reputation" and r["points"] == 45 for r in d["reasons"])
    finally:
        ctx.__exit__(None, None, None)


def test_reputation_match_raises_score_clean_does_not(settings, checker):
    ctx, c, h, _ = run(settings, checker, SB())
    clean_score = by_host(web_items(c, h))["acmebnk.net"]["score"]
    ctx.__exit__(None, None, None)
    ctx, c, h, _ = run(settings, checker, SB(matches=[TYPO]))
    try:
        hit = by_host(web_items(c, h))["acmebnk.net"]
        assert hit["score"] == min(100, clean_score + 45) and hit["severity"] in ("HIGH", "CRITICAL", "MEDIUM")
    finally:
        ctx.__exit__(None, None, None)


def test_no_key_means_unavailable_not_clean_and_no_request(settings, checker):
    sb = SB()
    ctx, c, h, scan = run(settings, checker, sb, key=None)
    try:
        assert sb.requests == []
        d = c.get(f"/api/detections/{by_host(web_items(c, h))['acmebnk.net']['id']}", headers=h).json()
        rep = next(s for s in d["signals"] if s["id"] == "url_reputation")
        assert rep["status"] == "unavailable" and rep["value"] is None and rep["checked_at"] is None
        assert d["reputation"]["verdict"] == "unknown" and d["reputation"]["reason"] == "no_key"
        assert scan["counts"]["reputation_unavailable"] == 2 and scan["counts"]["reputation_clean"] == 0 and scan["status"] == "completed"
    finally:
        ctx.__exit__(None, None, None)


@pytest.mark.parametrize("status, headers", [(429, {"retry-after": "60"}), (403, {}), (500, {})])
def test_provider_errors_never_fail_the_scan_or_look_clean(settings, checker, status, headers):
    ctx, c, h, scan = run(settings, checker, SB(status=status, headers=headers))
    try:
        assert scan["status"] in ("completed", "partial") and scan["counts"]["reputation_clean"] == 0 and scan["counts"]["reputation_unavailable"] == 2
        d = c.get(f"/api/detections/{by_host(web_items(c, h))['acmebnk.net']['id']}", headers=h).json()
        assert next(s for s in d["signals"] if s["id"] == "url_reputation")["status"] == "unavailable"
        assert len(web_items(c, h)) == 2  # the detections still exist, scored without reputation
    finally:
        ctx.__exit__(None, None, None)


def test_per_scan_lookup_limit(settings, checker):
    s = Settings(_env_file=None, data_dir=settings.data_dir, database_url="sqlite://", google_safe_browsing_api_key=KEY, safe_browsing_max_urls_per_scan=1)  # type: ignore[call-arg]
    sb = SB()
    ctx = make_client(s, checker, [StaticCollector(candidates())], transport=httpx.MockTransport(sb))
    c = ctx.__enter__()
    try:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        setup_brand(c, h)
        scan = run_scan(c, h)
        assert len(sb.sent) == 1 and scan["counts"]["reputation_over_limit"] == 1
    finally:
        ctx.__exit__(None, None, None)


def test_sample_web_candidates_are_synthetic_and_never_sent_to_google(settings, checker):
    sb = SB()
    ctx = make_client(live_settings(settings), checker, [SampleCollector()], transport=httpx.MockTransport(sb))
    c = ctx.__enter__()
    try:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "sample")
        setup_brand(c, h)
        scan = run_scan(c, h)
        items = web_items(c, h)
        assert sb.requests == [] and len(items) >= 3 and all(i["source"] == "sample" and i["handle"].endswith(".test") for i in items)
        assert scan["counts"]["reputation_skipped_sample"] >= 3 and scan["counts"]["official_excluded"] >= 4
        d = c.get(f"/api/detections/{items[0]['id']}", headers=h).json()
        assert {s["provenance"] for s in d["signals"]} == {"synthetic"} and d["reputation"]["verdict"] == "unavailable"
        # hard negative (acme-bakery) is never a detection
        assert not any("bakery" in i["handle"] for i in items)
        assert {i["status"] for i in all_detections(c, h)} == {"new"}  # the engine never confirms anything
    finally:
        ctx.__exit__(None, None, None)


# ---- API: filters, counts, dashboard -----------------------------------------------------------------


def test_filters_counts_and_dashboard_include_web(settings, checker):
    ctx, c, h, _ = run(settings, checker, SB(matches=[BAD]))
    try:
        everything = all_detections(c, h)
        web_n = sum(i["category"] == "web" for i in everything)
        assert web_n == 2 and len(everything) == 3
        assert len(web_items(c, h)) == 2
        assert len(all_detections(c, h, type="web")) == 2 and len(all_detections(c, h, type="lookalike_domain")) == 2
        assert len(all_detections(c, h, category="social")) == 1 and all_detections(c, h, category="apps") == []
        assert all(i["category"] == "social" for i in all_detections(c, h, type="social"))
        assert len(all_detections(c, h, category="web", platform="web")) == 2 and all_detections(c, h, category="web", platform="instagram") == []
        sev = {i["severity"] for i in web_items(c, h)}
        for s in sev:
            assert all(i["severity"] == s for i in web_items(c, h, severity=s))
        listing = c.get("/api/detections", params={"limit": 200}, headers=h).json()
        assert listing["counts"]["by_type"]["lookalike_domain"] == 2 and listing["counts"]["by_category"] == {"social": 1, "web": 2}
        assert sum(listing["counts"]["by_severity"].values()) == 3
        d = c.get("/api/dashboard", headers=h).json()
        assert d["kpis"]["total_detections"]["value"] == 3 == d["total_open"] and d["kpis"]["web_threats"]["value"] == 2
        assert d["counts"]["by_category"] == {"social": 1, "apps": 0, "web": 2} and d["counts"]["by_type"]["lookalike_domain"] == 2
        assert any(t["type"] == "lookalike_domain" and t["count"] == 2 for t in d["by_type"]) and sum(t["percent"] for t in d["by_type"]) == 100
        assert sum(sv for sv in d["counts"]["by_severity"].values()) == 3 and d["by_platform"][0] == {"platform": "web", "count": 2}
        assert c.get("/api/detections", params={"type": "bogus"}, headers=h).status_code == 422
    finally:
        ctx.__exit__(None, None, None)


def test_social_and_app_results_are_unchanged_by_web_candidates(settings, checker):
    ctx, c, h, _ = run(settings, checker, SB(), cands=[social("instagram", "acmebnk", "Acme Bnk")])
    only = all_detections(c, h)
    ctx.__exit__(None, None, None)
    ctx, c, h, _ = run(settings, checker, SB(), cands=candidates())
    try:
        mixed = [i for i in all_detections(c, h) if i["category"] == "social"]
        assert [(i["score"], i["risk_band"], i["severity"]) for i in mixed] == [(i["score"], i["risk_band"], i["severity"]) for i in only]
    finally:
        ctx.__exit__(None, None, None)


def test_human_decisions_survive_a_rescan_and_web_cannot_be_marked_official(settings, checker):
    ctx, c, h, _ = run(settings, checker, SB())
    try:
        det = by_host(web_items(c, h))["acmebnk.net"]
        assert c.post(f"/api/detections/{det['id']}/status", json={"status": "dismissed", "note": "ours"}, headers=h).status_code == 200
        r = c.post(f"/api/detections/{det['id']}/mark-official", json={}, headers=h)
        assert r.status_code == 422 and "official domains" in r.json()["detail"]
        scan2 = run_scan(c, h)
        assert scan2["counts"]["detections_protected"] >= 1
        assert by_host(web_items(c, h, status=["dismissed"]))["acmebnk.net"]["status"] == "dismissed"
    finally:
        ctx.__exit__(None, None, None)


def test_sources_endpoint_reports_the_reputation_provider_without_the_key(settings, checker):
    ctx, c, h, _ = run(settings, checker, SB())
    try:
        rep = c.get("/api/sources", headers=h).json()["reputation"]
        assert rep["configured"] is True and rep["provider"] == "google_safe_browsing" and rep["quota"]["budget"] == 5000 and rep["quota"]["used"] == 1
        assert KEY not in json.dumps(rep)
    finally:
        ctx.__exit__(None, None, None)
    ctx2, c2, h2, _ = run(settings, checker, SB(), key=None)
    try:
        assert c2.get("/api/sources", headers=h2).json()["reputation"]["configured"] is False
    finally:
        ctx2.__exit__(None, None, None)

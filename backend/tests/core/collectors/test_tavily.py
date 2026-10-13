import json
from datetime import UTC, datetime

import httpx
import pytest

from app.core.collectors.base import BrandQuery, SourceUnavailable
from app.core.collectors.credits import PROVIDER, REPORTED, credit_cost
from app.core.collectors.tavily import PLATFORM_DOMAINS, build_queries
from app.models import ApiUsage

from .helpers import BRAND, NOW, TEST_KEY, ctx, make_ledger, make_tavily, memory_db, respond


def ok_handler(calls: list):
    def h(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return respond(200, fixture="tavily/search_ok.json")

    return h


def test_normalizes_fixture_into_candidates_and_drops_non_profiles():
    calls: list = []
    c = make_tavily(ok_handler(calls), max_queries_per_scan=1)
    out = c.collect(BRAND, ctx())
    keys = {(x.platform, x.handle) for x in out}
    assert ("instagram", "acrne_bank_support") in keys
    assert ("facebook", "acmebanksupport.help") in keys
    assert ("x", "acme_rewards24") in keys
    assert ("telegram", "acmebank_helpline") in keys
    assert ("linkedin", "acme-bank-care") in keys
    assert ("google_play", "com.acmebnak.support") in keys
    assert ("app_store", "com.acme.bank.mod") not in keys
    assert ("third_party_apk", "com.acme.bank.mod") in keys
    # the post URL and the video URL are not profiles; the duplicate instagram result is merged
    assert len(out) == 9  # 9 distinct profiles/listings in the fixture
    ig = next(x for x in out if x.handle == "acrne_bank_support")
    assert ig.display_name == "Acme Bank Support Care"
    assert ig.bio.startswith("Official helpline")
    assert ig.profile_url == "https://www.instagram.com/acrne_bank_support/"
    assert ig.extras["score"] == 0.91
    app = next(x for x in out if x.platform == "app_store")
    assert app.extras["apple_app_id"] == "1234567890"
    assert app.display_name == "Acme Bank Rewards"
    apk = next(x for x in out if x.platform == "third_party_apk")
    assert apk.extras["mirror"] == "apkpure" and apk.display_name == "Acme Bank"


def test_request_shape_auth_domains_exact_match_and_no_key_in_body():
    calls: list = []
    c = make_tavily(ok_handler(calls), max_queries_per_scan=2)
    c.collect(BRAND, ctx())
    assert len(calls) == 2
    req = calls[0]
    assert req.method == "POST" and str(req.url) == "https://api.tavily.com/search"
    assert req.headers["authorization"] == f"Bearer {TEST_KEY}"
    body = json.loads(req.content)
    assert body["exact_match"] is True
    assert body["search_depth"] == "basic"
    assert body["query"] == '"Acme Bank" support'
    for d in ("instagram.com", "facebook.com", "x.com", "twitter.com", "t.me", "linkedin.com", "youtube.com", "play.google.com", "apps.apple.com", "apkpure.com"):
        assert d in body["include_domains"]
    assert TEST_KEY not in req.content.decode()


def test_include_domains_follow_monitored_platforms():
    calls: list = []
    c = make_tavily(ok_handler(calls), max_queries_per_scan=1)
    c.collect(BRAND, ctx(platforms=frozenset({"instagram", "google_play"})))
    domains = json.loads(calls[0].content)["include_domains"]
    assert "instagram.com" in domains and "play.google.com" in domains
    assert "facebook.com" not in domains and "apkpure.com" not in domains


def test_queries_are_quoted_combine_lure_words_and_include_variants():
    qs = build_queries(BRAND)
    assert qs[:4] == ['"Acme Bank" support', '"Acme Bank" official', '"Acme Bank" helpline', '"Acme Bank" rewards']
    assert '"Acme" official' in qs and '"acmebank" support' in qs
    variants = [q for q in qs if q.count('"') == 2 and q.endswith('"')]
    assert variants, "look-alike variants are queried too"
    assert all(q.count('"') % 2 == 0 for q in qs)
    assert len(qs) == len(set(qs))
    assert build_queries(BrandQuery(brand_id=1, name='  ')) == []
    assert '"' not in build_queries(BrandQuery(brand_id=1, name='Ac"me'))[0][1:-9]  # quotes in names are stripped


def test_per_scan_query_cap_setting_and_context_cap():
    calls: list = []
    make_tavily(ok_handler(calls), max_queries_per_scan=3).collect(BRAND, ctx())
    assert len(calls) == 3
    calls.clear()
    make_tavily(ok_handler(calls), max_queries_per_scan=12).collect(BRAND, ctx(max_queries=2))
    assert len(calls) == 2
    calls.clear()
    with pytest.raises(SourceUnavailable) as e:
        make_tavily(ok_handler(calls), max_queries_per_scan=0).collect(BRAND, ctx())
    assert e.value.reason == "not_configured" and calls == []


def test_missing_key_is_unavailable_not_a_crash_and_makes_no_call():
    calls: list = []
    for key in (None, "", "   "):
        c = make_tavily(ok_handler(calls), key=key)
        assert not c.is_configured()
        with pytest.raises(SourceUnavailable) as e:
            c.collect(BRAND, ctx())
        assert e.value.reason == "no_key"
        assert "TAVILY_API_KEY" in e.value.message
    assert calls == []


def test_credits_counted_persisted_and_reported_usage_recorded():
    sf = memory_db()
    ledger = make_ledger(sf)
    make_tavily(ok_handler([]), ledger=ledger, max_queries_per_scan=3).collect(BRAND, ctx())
    snap = ledger.snapshot()
    assert snap["used"] == 3 and snap["remaining"] == 897 and snap["month"] == "2026-10" and snap["reported_used"] == 3
    # persistent: a brand-new ledger object over the same database sees the counter
    assert make_ledger(sf).snapshot()["used"] == 3
    with sf() as db:
        rows = {(r.provider, r.month): r.credits for r in db.query(ApiUsage)}
    assert rows == {(PROVIDER, "2026-10"): 3, (REPORTED, "2026-10"): 3}


def test_counter_rolls_over_per_month():
    sf = memory_db()
    month = {"now": NOW}
    ledger = make_ledger(sf, now=lambda: month["now"])
    assert ledger.reserve(5) and ledger.snapshot()["used"] == 5
    month["now"] = datetime(2026, 11, 1, 0, 0, 1, tzinfo=UTC)
    assert ledger.snapshot()["used"] == 0 and ledger.snapshot()["month"] == "2026-11"


def test_budget_guard_blocks_call_when_remaining_is_less_than_cost():
    calls: list = []
    ledger = make_ledger(budget=2)
    c = make_tavily(ok_handler(calls), ledger=ledger, max_queries_per_scan=12)
    out = c.collect(BRAND, ctx())  # two calls fit, the third is refused: partial results are kept
    assert len(calls) == 2 and out
    assert any("budget" in w for w in c.warnings)
    assert ledger.snapshot()["remaining"] == 0
    # budget exhausted: no call at all, unavailable with reason 'budget'
    with pytest.raises(SourceUnavailable) as e:
        c.collect(BRAND, ctx())
    assert e.value.reason == "budget" and len(calls) == 2
    assert "2 of 2" in e.value.message


def test_advanced_depth_costs_two_and_needs_two_remaining():
    assert credit_cost("basic") == 1 and credit_cost("advanced") == 2
    calls: list = []
    ledger = make_ledger(budget=3)
    c = make_tavily(ok_handler(calls), ledger=ledger, search_depth="advanced", max_queries_per_scan=5)
    c.collect(BRAND, ctx())
    assert len(calls) == 1 and ledger.snapshot()["remaining"] == 1  # 1 left < cost 2 -> refused
    assert json.loads(calls[0].content)["search_depth"] == "advanced"


def test_zero_budget_never_calls():
    calls: list = []
    with pytest.raises(SourceUnavailable) as e:
        make_tavily(ok_handler(calls), ledger=make_ledger(budget=0)).collect(BRAND, ctx())
    assert e.value.reason == "budget" and calls == []


def test_api_reported_usage_higher_than_counted_tightens_the_guard():
    ledger = make_ledger(budget=10)
    ledger.record_reported(9)
    assert ledger.snapshot()["used"] == 9
    assert ledger.reserve(1)  # 9 + 1 fits
    ledger.record_reported(1)  # the API confirms the call: 10 of 10
    assert not ledger.reserve(1)
    assert ledger.snapshot()["remaining"] == 0


@pytest.mark.parametrize(
    "status,headers,reason,retry_after",
    [
        (401, {}, "invalid_key", None),
        (429, {"Retry-After": "37"}, "rate_limited", 37.0),
        (429, {}, "rate_limited", None),
        (432, {}, "quota", None),
        (433, {}, "quota", None),
        (500, {}, "unreachable", None),
        (502, {"Retry-After": "5"}, "unreachable", 5.0),
        (400, {}, "bad_request", None),
    ],
)
def test_error_statuses_map_to_unavailable_reasons(status, headers, reason, retry_after):
    fixture = {401: "error_401", 429: "error_429", 432: "error_432", 433: "error_433"}.get(status, "error_500")

    def h(request):
        return respond(status, fixture=f"tavily/{fixture}.json", headers=headers)

    with pytest.raises(SourceUnavailable) as e:
        make_tavily(h).collect(BRAND, ctx())
    assert e.value.reason == reason
    assert e.value.retry_after == retry_after
    assert TEST_KEY not in e.value.message


def test_not_billed_errors_are_refunded_but_server_errors_and_timeouts_keep_the_reservation():
    for status, expected_used in ((401, 0), (429, 0), (432, 0), (500, 1)):
        ledger = make_ledger()
        with pytest.raises(SourceUnavailable):
            make_tavily(lambda r, s=status: respond(s, body="{}"), ledger=ledger).collect(BRAND, ctx())
        assert ledger.snapshot()["used"] == expected_used, status

    def timeout(request):
        raise httpx.ReadTimeout("t", request=request)

    ledger = make_ledger()
    with pytest.raises(SourceUnavailable) as e:
        make_tavily(timeout, ledger=ledger).collect(BRAND, ctx())
    assert e.value.reason == "timeout" and ledger.snapshot()["used"] == 1

    def down(request):
        raise httpx.ConnectError("x", request=request)

    ledger = make_ledger()
    with pytest.raises(SourceUnavailable) as e:
        make_tavily(down, ledger=ledger).collect(BRAND, ctx())
    assert e.value.reason == "unreachable" and ledger.snapshot()["used"] == 0


def test_429_mid_scan_keeps_partial_results_and_reports_a_warning():
    n = {"i": 0}

    def h(request):
        n["i"] += 1
        if n["i"] == 3:
            return respond(429, body="{}", headers={"Retry-After": "30"})
        return respond(200, fixture="tavily/search_ok.json")

    c = make_tavily(h, max_queries_per_scan=6)
    out = c.collect(BRAND, ctx())
    assert out and n["i"] == 3
    assert c.warnings and "Stopped after 2 of 6" in c.warnings[0]


def test_malformed_json_is_bad_response():
    with pytest.raises(SourceUnavailable) as e:
        make_tavily(lambda r: respond(200, fixture="tavily/malformed.txt")).collect(BRAND, ctx())
    assert e.value.reason == "bad_response"


def test_api_key_never_appears_in_logs_or_exceptions(caplog):
    caplog.set_level("DEBUG")
    with pytest.raises(SourceUnavailable) as e:
        make_tavily(lambda r: respond(401, fixture="tavily/error_401.json")).collect(BRAND, ctx())
    assert TEST_KEY not in str(e.value) and TEST_KEY not in caplog.text
    assert PLATFORM_DOMAINS["x"] == ("x.com", "twitter.com")

"""Safe Browsing adapter with mocked HTTP only (httpx.MockTransport). No live call is ever made."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.collectors.util import TTLCache
from app.core.providers.safe_browsing import ENDPOINT, MAX_URLS_PER_REQUEST, DailyLedger, SafeBrowsingClient
from app.db.base import Base
from app.db.session import make_engine, make_session_factory

KEY = "AIza-test-key-not-real-0123456789"
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
URL = "https://acmebank.com.evil.test/login"


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        return self.t


class Server:
    """Records requests and answers with a canned response (or raises)."""

    def __init__(self, handler=None) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = handler or (lambda req: httpx.Response(200, json={}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def client_for(server: Server, *, key: str | None = KEY, ledger=None, cache=None, clock=None) -> SafeBrowsingClient:
    transport = httpx.MockTransport(server)
    return SafeBrowsingClient(key, client_factory=lambda: httpx.Client(transport=transport), ledger=ledger, cache=cache, now=clock or Clock())


def match_body(*urls: str, threat="SOCIAL_ENGINEERING") -> dict:
    return {"matches": [{"threatType": threat, "platformType": "ANY_PLATFORM", "threatEntryType": "URL", "threat": {"url": u}, "cacheDuration": "300s"} for u in urls]}


@pytest.fixture
def sf():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


# ---- verdicts -------------------------------------------------------------------------------------


def test_match():
    srv = Server(lambda r: httpx.Response(200, json=match_body(URL)))
    r = client_for(srv).check(URL)
    assert r.verdict == "match" and r.threat_types == ("SOCIAL_ENGINEERING",) and r.checked_at == NOW and r.provider == "google_safe_browsing"
    assert r.signal_status == "present" and r.to_dict()["provenance"] == "real_api" and "SOCIAL_ENGINEERING" in r.message


def test_clean_is_not_a_safety_guarantee():
    r = client_for(Server()).check(URL)  # Google answers {} for no match
    assert r.verdict == "clean" and r.checked_at == NOW and r.signal_status == "clean"
    assert "does not prove" in r.message and "safe" in r.message.lower()


def test_request_shape_key_in_header_only():
    srv = Server()
    client_for(srv).check(URL)
    req = srv.requests[0]
    assert req.method == "POST" and str(req.url) == ENDPOINT == "https://safebrowsing.googleapis.com/v4/threatMatches:find"
    assert KEY not in str(req.url) and req.headers["x-goog-api-key"] == KEY  # never in the URL, so it cannot leak through an error message
    body = srv.bodies[0]
    assert body["threatInfo"]["threatEntryTypes"] == ["URL"] and body["threatInfo"]["threatEntries"] == [{"url": URL}]
    assert {"MALWARE", "SOCIAL_ENGINEERING"} <= set(body["threatInfo"]["threatTypes"]) and body["client"]["clientId"]


def test_missing_key_makes_no_request_and_is_unknown():
    srv = Server()
    c = client_for(srv, key=None)
    r = c.check(URL)
    assert srv.requests == [] and r.verdict == "unknown" and r.reason == "no_key" and r.checked_at is None and r.signal_status == "unavailable"
    assert c.configured is False and c.status()["configured"] is False and KEY not in json.dumps(c.status())
    assert client_for(Server(), key="   ").configured is False


@pytest.mark.parametrize("code", [400, 401, 403])
def test_client_errors_are_failed_with_a_hint(code):
    srv = Server(lambda r: httpx.Response(code, json={"error": {"code": code, "message": "API key not valid", "status": "INVALID_ARGUMENT"}}))
    r = client_for(srv).check(URL)
    assert r.verdict == "failed" and r.reason == "invalid_key" and "key" in r.message and r.signal_status == "unavailable"
    assert KEY not in r.message


def test_429_is_unknown_quota_and_blocks_further_requests_until_retry_after():
    clock = Clock()
    srv = Server(lambda r: httpx.Response(429, headers={"retry-after": "120"}, json={"error": {"status": "RESOURCE_EXHAUSTED"}}))
    c = client_for(srv, clock=clock)
    r = c.check(URL)
    assert r.verdict == "unknown" and r.reason == "quota" and len(srv.requests) == 1
    again = c.check("https://other.test/")  # blocked: no request
    assert again.verdict == "unknown" and again.reason == "quota" and again.checked_at is None and len(srv.requests) == 1
    clock.t = NOW + timedelta(seconds=121)
    srv.handler = lambda req: httpx.Response(200, json={})
    assert c.check("https://other.test/").verdict == "clean" and len(srv.requests) == 2


def test_403_with_a_quota_message_is_unknown_not_failed():
    srv = Server(lambda r: httpx.Response(403, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded for quota metric"}}))
    r = client_for(srv).check(URL)
    assert r.verdict == "unknown" and r.reason == "quota"


def test_timeout_is_failed():
    def boom(_r):
        raise httpx.ReadTimeout("slow")

    r = client_for(Server(boom)).check(URL)
    assert r.verdict == "failed" and r.reason == "timeout" and r.signal_status == "unavailable"


def test_network_error_and_server_error_are_failed():
    def down(_r):
        raise httpx.ConnectError("no route")

    assert client_for(Server(down)).check(URL).reason == "unreachable"
    r = client_for(Server(lambda r: httpx.Response(503, text="oops"))).check(URL)
    assert r.verdict == "failed" and r.reason == "server_error"


@pytest.mark.parametrize("response", [httpx.Response(200, text="<html>not json</html>"), httpx.Response(200, json=[1, 2]), httpx.Response(200, json={"matches": "x"}),
                                      httpx.Response(302, headers={"location": "http://x"})])
def test_malformed_responses_are_failed_never_clean(response):
    r = client_for(Server(lambda req: response)).check(URL)
    assert r.verdict == "failed" and r.reason == "bad_response" and r.signal_status == "unavailable"


# ---- batching, attribution, cache --------------------------------------------------------------------


def test_batch_maps_matches_to_the_right_urls():
    urls = ["https://a.test/", "https://b.test/", "https://c.test/"]
    srv = Server(lambda r: httpx.Response(200, json=match_body("https://b.test/")))
    res = client_for(srv).check_urls(urls)
    assert [res[u].verdict for u in urls] == ["clean", "match", "clean"] and len(srv.requests) == 1


def test_unattributable_match_is_unknown_for_the_rest():
    srv = Server(lambda r: httpx.Response(200, json=match_body("https://not-sent.test/")))
    res = client_for(srv).check_urls(["https://a.test/", "https://b.test/"])
    assert {r.verdict for r in res.values()} == {"unknown"} and {r.reason for r in res.values()} == {"unattributed_match"}


def test_more_than_500_urls_split_into_several_requests():
    urls = [f"https://d{i}.test/" for i in range(MAX_URLS_PER_REQUEST + 5)]
    srv = Server()
    res = client_for(srv).check_urls(urls)
    assert len(res) == len(urls) and [len(b["threatInfo"]["threatEntries"]) for b in srv.bodies] == [500, 5]


def test_duplicates_are_sent_once():
    srv = Server()
    client_for(srv).check_urls([URL, URL, ""])
    assert len(srv.bodies[0]["threatInfo"]["threatEntries"]) == 1


def test_cache_answers_repeat_lookups_without_a_request():
    srv = Server(lambda r: httpx.Response(200, json=match_body(URL)))
    c = client_for(srv, cache=TTLCache(3600))
    first = c.check(URL)
    second = c.check(URL)
    assert len(srv.requests) == 1 and second.cached is True and second.verdict == "match" and first.cached is False


def test_failures_are_not_cached():
    srv = Server(lambda r: httpx.Response(500))
    c = client_for(srv, cache=TTLCache(3600))
    c.check(URL)
    c.check(URL)
    assert len(srv.requests) == 2


# ---- daily budget ----------------------------------------------------------------------------------


def test_daily_budget_stops_requests_and_resets_next_day(sf):
    clock = Clock()
    ledger = DailyLedger(sf, 2, now=clock)
    srv = Server()
    c = client_for(srv, ledger=ledger, clock=clock)
    assert [c.check(f"https://x{i}.test/").verdict for i in range(2)] == ["clean", "clean"]
    r = c.check("https://x3.test/")
    assert r.verdict == "unknown" and r.reason == "budget" and r.checked_at is None and len(srv.requests) == 2
    assert ledger.snapshot() == {"provider": "google_safe_browsing", "unit": "requests", "period": "UTC day", "used": 2, "budget": 2, "remaining": 0}
    clock.t = NOW + timedelta(days=1)
    assert c.check("https://x4.test/").verdict == "clean" and ledger.snapshot()["used"] == 1


def test_one_batched_request_costs_one_unit(sf):
    ledger = DailyLedger(sf, 10, now=Clock())
    client_for(Server(), ledger=ledger).check_urls([f"https://d{i}.test/" for i in range(50)])
    assert ledger.snapshot()["used"] == 1


def test_status_is_secret_free_and_reports_last_result(sf):
    c = client_for(Server(lambda r: httpx.Response(429)), ledger=DailyLedger(sf, 5, now=Clock()))
    c.check(URL)
    st = c.status()
    assert st["configured"] is True and st["last"]["reason"] == "quota" and st["quota"]["budget"] == 5 and st["blocked_until"]
    assert KEY not in json.dumps(st)

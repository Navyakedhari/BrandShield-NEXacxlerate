"""Shared contract every collector must satisfy, run against every collector with hostile inputs."""

import httpx
import pytest

from app.core.collectors.base import SOURCE_PLATFORMS, Candidate, SourceUnavailable

from .helpers import BRAND, ctx, make_itunes, make_play, make_sample, make_searxng, make_tavily, respond

HTTP_COLLECTORS = {
    "tavily": (make_tavily, "tavily"),
    "searxng": (make_searxng, "searxng"),
    "itunes": (make_itunes, "itunes"),
}

# scenario -> (status, fixture file or raw body)
SCENARIOS = {
    "ok": (200, "search_ok.json"),
    "empty": (200, "search_empty.json"),
    "missing_fields": (200, "search_missing_fields.json"),
    "malformed": (200, "malformed.txt"),
    "json_list": (200, "[1, 2, 3]"),
    "json_scalar": (200, "42"),
    "results_not_list": (200, '{"results": "nope", "resultCount": 1}'),
    "http_401": (401, "error_401.json"),
    "http_403": (403, "search_empty.json"),
    "http_404": (404, "search_empty.json"),
    "http_429": (429, "search_empty.json"),
    "http_432": (432, "search_empty.json"),
    "http_500": (500, "search_empty.json"),
    "http_503": (503, "malformed.txt"),
    "timeout": (None, "timeout"),
    "connect_error": (None, "connect"),
}


def _handler(folder: str, scenario: str):
    status, what = SCENARIOS[scenario]

    def handler(request: httpx.Request) -> httpx.Response:
        if status is None:
            raise httpx.ReadTimeout("slow", request=request) if what == "timeout" else httpx.ConnectError("down", request=request)
        if what.endswith((".json", ".txt")):
            from .helpers import FIXTURES

            path = FIXTURES / folder / what
            if not path.exists():  # e.g. itunes has no error_401.json: reuse the empty body
                path = FIXTURES / folder / "search_empty.json"
            return respond(status, body=path.read_text(encoding="utf-8"))
        return respond(status, body=what)

    return handler


def assert_contract(collector, result_or_exc):
    if isinstance(result_or_exc, SourceUnavailable):
        assert result_or_exc.reason and isinstance(result_or_exc.reason, str)
        assert result_or_exc.message
        assert result_or_exc.retry_after is None or result_or_exc.retry_after >= 0
        return
    assert isinstance(result_or_exc, list)
    keys = set()
    for c in result_or_exc:
        assert isinstance(c, Candidate)
        assert c.kind in ("social", "app")
        assert c.platform in (*SOURCE_PLATFORMS, "youtube")
        assert c.source == collector.name
        assert c.fetched_at.tzinfo is not None
        assert c.handle or c.profile_url
        assert c.extras is not None and isinstance(c.extras, dict)
        assert c.data_source == "live"
        assert c.key not in keys, "duplicates must be merged"
        keys.add(c.key)
        c.to_engine()  # must convert without error
        Candidate.from_dict(c.to_dict())  # must survive the cache round trip
        import json

        json.dumps(c.to_dict())  # cache must be JSON-safe


def run(collector):
    try:
        return collector.collect(BRAND, ctx())
    except SourceUnavailable as exc:
        return exc


@pytest.mark.parametrize("scenario", list(SCENARIOS))
@pytest.mark.parametrize("name", list(HTTP_COLLECTORS))
def test_http_collectors_only_raise_source_unavailable(name, scenario):
    make, folder = HTTP_COLLECTORS[name]
    c = make(_handler(folder, scenario))
    outcome = run(c)  # any other exception type fails the test
    assert_contract(c, outcome)
    if scenario in ("ok",):
        assert isinstance(outcome, list) and outcome
    if scenario in ("empty", "missing_fields", "results_not_list"):
        assert isinstance(outcome, list)
    if scenario in ("malformed", "json_list", "json_scalar", "http_500", "http_503", "timeout", "connect_error"):
        assert isinstance(outcome, SourceUnavailable)


PLAY_SCENARIOS = {
    "ok": lambda *a, **k: [
        {"appId": "com.acmebnak.support", "title": "Acme Bnak Mobile", "developer": "Acme Rewards", "icon": "https://x/i.png", "score": 2.1, "installs": "1,000+", "description": "d"}
    ],
    "empty": lambda *a, **k: [],
    "missing_fields": lambda *a, **k: [{}, {"appId": "com.only.id"}, "junk", None, {"title": "no id"}, {"appId": 5, "score": "n/a"}],
    "malformed": lambda *a, **k: "garbage",
    "none": lambda *a, **k: None,
}


def _boom(*a, **k):
    raise RuntimeError("scraper exploded with details that must not leak: SECRET123")


@pytest.mark.parametrize("scenario", [*PLAY_SCENARIOS, "raises"])
def test_google_play_contract(scenario):
    c = make_play(_boom if scenario == "raises" else PLAY_SCENARIOS[scenario])
    outcome = run(c)
    assert_contract(c, outcome)
    if scenario == "raises":
        assert isinstance(outcome, SourceUnavailable)
        assert "SECRET123" not in outcome.message
    if scenario == "ok":
        assert outcome[0].handle == "com.acmebnak.support"


def test_sample_contract():
    c = make_sample()
    out = c.collect(BRAND, ctx())
    assert out and all(isinstance(x, Candidate) for x in out)
    assert all(x.source == "sample" and x.data_source == "sample" for x in out)
    assert all(x.fetched_at.tzinfo for x in out)
    for x in out:
        x.to_engine()


@pytest.mark.parametrize("name", ["tavily", "searxng", "itunes"])
def test_collectors_do_not_mutate_brand_or_leak_state_between_calls(name):
    make, folder = HTTP_COLLECTORS[name]
    c = make(_handler(folder, "ok"))
    first = run(c)
    c.warnings.append("stale note")
    second = run(c)
    assert c.warnings != ["stale note"], "warnings must reset on every call"
    assert isinstance(first, list) and isinstance(second, list)

import pytest

from app.core.collectors.base import SourceUnavailable
from app.core.collectors.google_play import GooglePlayCollector
from app.core.collectors.util import SpacingLimiter, TTLCache
from app.core.pipeline.url_safety import UnsafeURLError

from .helpers import BRAND, FakeClock, ctx, make_itunes, make_play, make_searxng, respond

# ---------------------------------------------------------------------------------------- SearXNG


def test_searxng_normalizes_and_drops_non_profile_urls():
    calls: list = []

    def h(request):
        calls.append(request)
        return respond(200, fixture="searxng/search_ok.json")

    out = make_searxng(h, max_queries_per_scan=1).collect(BRAND, ctx())
    assert {(c.platform, c.handle) for c in out} == {
        ("instagram", "acrne_bank_support"), ("facebook", "acmebanksupport.help"), ("telegram", "acmebank_helpline"), ("x", "acme_rewards24"),
    }
    assert next(c for c in out if c.platform == "x").display_name == "Acme Rewards"
    assert next(c for c in out if c.platform == "instagram").extras["engine"] == "duckduckgo"
    url = calls[0].url
    assert url.path == "/search" and url.params["format"] == "json" and url.params["q"] == '"Acme Bank" support'


def test_searxng_403_explains_how_to_enable_json():
    with pytest.raises(SourceUnavailable) as e:
        make_searxng(lambda r: respond(403, fixture="searxng/error_403.html")).collect(BRAND, ctx())
    assert e.value.reason == "json_disabled"
    assert "search.formats" in e.value.message or "formats: [html, json]" in e.value.message
    assert "settings.yml" in e.value.message


def test_searxng_429_and_5xx_and_unset_base():
    with pytest.raises(SourceUnavailable) as e:
        make_searxng(lambda r: respond(429, body="{}", headers={"Retry-After": "12"})).collect(BRAND, ctx())
    assert (e.value.reason, e.value.retry_after) == ("rate_limited", 12.0)
    with pytest.raises(SourceUnavailable) as e:
        make_searxng(lambda r: respond(502, body="x")).collect(BRAND, ctx())
    assert e.value.reason == "unreachable"
    c = make_searxng(lambda r: respond(200, body="{}"), base=None)
    assert not c.is_configured()
    with pytest.raises(SourceUnavailable) as e:
        c.collect(BRAND, ctx())
    assert e.value.reason == "not_configured"


def test_searxng_ssrf_private_base_url_refused_unless_operator_allows():
    calls: list = []

    def h(request):
        calls.append(request)
        return respond(200, fixture="searxng/search_empty.json")

    for base in ("http://127.0.0.1:8080", "http://10.0.0.5", "http://localhost:8888", "http://192.168.1.20", "http://169.254.169.254"):
        with pytest.raises(SourceUnavailable) as e:
            make_searxng(h, base=base).collect(BRAND, ctx())
        assert e.value.reason == "unsafe_base_url" and "SEARXNG_ALLOW_PRIVATE_HOSTS" in e.value.message
    # a public-looking name that resolves to a private address is refused too (DNS rebinding style)
    with pytest.raises(SourceUnavailable):
        make_searxng(h, base="https://searx.example.org", resolver=lambda host: ["10.1.2.3"]).collect(BRAND, ctx())
    assert calls == []
    out = make_searxng(h, base="http://10.0.0.5", allow_private_hosts=True).collect(BRAND, ctx())
    assert out == [] and calls
    # even with the opt-in, only http(s) without credentials
    for bad in ("file:///etc/passwd", "ftp://10.0.0.5", "http://user:pw@10.0.0.5"):
        with pytest.raises(SourceUnavailable) as e:
            make_searxng(h, base=bad, allow_private_hosts=True).collect(BRAND, ctx())
        assert e.value.reason == "unsafe_base_url"


def test_searxng_uses_the_shared_ssrf_validator(monkeypatch):
    seen: list = []

    def fake(url, resolver):
        seen.append(url)
        raise UnsafeURLError("nope")

    monkeypatch.setattr("app.core.collectors.searxng.validate_public_url", fake)
    with pytest.raises(SourceUnavailable) as e:
        make_searxng(lambda r: respond(200, body="{}")).collect(BRAND, ctx())
    assert seen == ["https://searx.example.org"] and e.value.reason == "unsafe_base_url"


# ---------------------------------------------------------------------------------------- iTunes


def test_itunes_normalizes_apps_and_tolerates_missing_fields():
    calls: list = []

    def h(request):
        calls.append(request)
        return respond(200, fixture="itunes/search_ok.json")

    out = make_itunes(h, max_queries_per_scan=1, country="IN").collect(BRAND, ctx())
    p = calls[0].url.params
    assert (p["country"], p["entity"], p["term"]) == ("in", "software", "Acme Bank") and p["limit"] == "25"
    by_id = {c.handle: c for c in out}
    a = by_id["com.acmebnak.rewards"]
    assert (a.platform, a.kind, a.store) == ("app_store", "app", "apple_app_store")
    assert a.display_name == "Acme Bank Rewards" and a.publisher == "Acme Rewards Pvt Ltd"
    assert a.icon_url.endswith("512x512bb.jpg") and a.rating == 2.3 and a.rating_count == 41
    assert a.profile_url == "https://apps.apple.com/app/acme-bank-rewards/id1234567890"  # tracking query stripped
    assert a.extras["apple_app_id"] == "1234567890" and a.extras["version"] == "1.0.2"
    eng = a.to_engine()
    assert eng.store == "apple_app_store" and eng.app_id == "1234567890"  # numeric id for the allowlist
    assert by_id["com.acmebank.mobile"].icon_url.endswith("100x100bb.jpg")  # falls back to artworkUrl100
    assert by_id["com.nourl.app"].profile_url == "https://apps.apple.com/app/id3333333333"


def test_itunes_missing_fields_fixture():
    out = make_itunes(lambda r: respond(200, fixture="itunes/search_missing_fields.json"), max_queries_per_scan=1).collect(BRAND, ctx())
    ids = {c.handle for c in out}
    assert {"com.only.bundle", "4444444444"} <= ids
    assert all(c.platform == "app_store" for c in out)
    junk = [c for c in out if c.handle is None or c.handle == ""]
    assert not junk


def test_itunes_rate_limiter_spaces_calls_with_fake_clock():
    clock = FakeClock()
    calls: list = []

    def h(request):
        calls.append(clock.t)
        return respond(200, fixture="itunes/search_empty.json")

    make_itunes(h, clock, max_queries_per_scan=4).collect(BRAND, ctx())
    assert len(calls) == 4
    gaps = [b - a for a, b in zip(calls, calls[1:], strict=False)]
    assert all(abs(g - 3.0) < 1e-9 for g in gaps)  # 20 calls/min -> one every 3 s
    assert clock.sleeps == [3.0, 3.0, 3.0]  # the first call does not wait


def test_spacing_limiter_unit():
    clock = FakeClock()
    lim = SpacingLimiter(20, clock=clock, sleep=clock.sleep)
    assert lim.wait() == 0
    clock.t += 1.0
    assert lim.wait() == pytest.approx(2.0)
    clock.t += 10
    assert lim.wait() == 0
    assert SpacingLimiter(0, clock=clock, sleep=clock.sleep).interval == 60.0  # never divides by zero


def test_itunes_response_cache_with_ttl_and_injectable_clock():
    clock = FakeClock()
    calls: list = []

    def h(request):
        calls.append(request)
        return respond(200, fixture="itunes/search_ok.json")

    c = make_itunes(h, clock, max_queries_per_scan=2)
    c.collect(BRAND, ctx())
    assert len(calls) == 2
    c.collect(BRAND, ctx())
    assert len(calls) == 2, "identical terms inside the TTL are served from cache"
    clock.t += 3601
    c.collect(BRAND, ctx())
    assert len(calls) == 4, "after the TTL the API is asked again"


def test_ttl_cache_unit_eviction_and_expiry():
    clock = FakeClock()
    cache = TTLCache(10, clock=clock, max_items=2)
    cache.set("a", 1)
    clock.t += 1
    cache.set("b", 2)
    clock.t += 1
    cache.set("c", 3)  # evicts the oldest
    assert cache.get("a") is None and cache.get("b") == 2 and cache.get("c") == 3 and len(cache) == 2
    clock.t += 20
    assert cache.get("b") is None


def test_itunes_errors_failed_responses_are_not_cached():
    clock = FakeClock()
    state = {"n": 0}

    def h(request):
        state["n"] += 1
        return respond(403, fixture="itunes/error_403.json", headers={"Retry-After": "90"}) if state["n"] == 1 else respond(200, fixture="itunes/search_ok.json")

    c = make_itunes(h, clock, max_queries_per_scan=1)
    with pytest.raises(SourceUnavailable) as e:
        c.collect(BRAND, ctx())
    assert (e.value.reason, e.value.retry_after) == ("rate_limited", 90.0)
    assert c.collect(BRAND, ctx())  # next call hits the API again


def test_itunes_skips_when_app_store_not_monitored():
    calls: list = []
    c = make_itunes(lambda r: calls.append(r) or respond(200, body="{}"))
    assert c.collect(BRAND, ctx(platforms=frozenset({"instagram"}))) == []
    assert calls == []


# ---------------------------------------------------------------------------------------- Google Play


def test_google_play_reports_not_installed_when_package_absent(monkeypatch):
    monkeypatch.setattr("app.core.collectors.google_play.importlib.import_module", lambda name: (_ for _ in ()).throw(ImportError(name)))
    c = GooglePlayCollector(installed=lambda: False)
    assert not c.is_configured()
    with pytest.raises(SourceUnavailable) as e:
        c.collect(BRAND, ctx())
    assert e.value.reason == "not_installed" and "DEPENDENCIES" in e.value.message


def test_google_play_lazy_import_uses_package_when_present(monkeypatch):
    import types

    fake = types.SimpleNamespace(search=lambda q, lang, country, n_hits: [{"appId": "com.x.y", "title": q, "developer": "D", "score": 3.5, "ratings": 12}])
    monkeypatch.setattr("app.core.collectors.google_play.importlib.import_module", lambda name: fake)
    out = GooglePlayCollector(installed=lambda: True, max_queries_per_scan=1).collect(BRAND, ctx())
    assert out[0].handle == "com.x.y" and out[0].publisher == "D" and out[0].rating_count == 12
    assert out[0].profile_url == "https://play.google.com/store/apps/details?id=com.x.y"
    assert out[0].store == "google_play" and out[0].to_engine().store == "google_play"


def test_google_play_partial_failure_keeps_results():
    n = {"i": 0}

    def search(q, **kw):
        n["i"] += 1
        if n["i"] == 2:
            raise RuntimeError("blocked")
        return [{"appId": f"com.app{n['i']}.x", "title": q}]

    c = make_play(search, max_queries_per_scan=4)
    out = c.collect(BRAND, ctx())
    assert [x.handle for x in out] == ["com.app1.x"] and c.warnings

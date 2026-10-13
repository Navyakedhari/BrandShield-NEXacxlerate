from datetime import UTC, datetime, timedelta

import pytest

from app.config.config import Settings
from app.core.collectors import orchestrator as orch
from app.core.collectors.base import BrandQuery, Candidate, CollectContext, Collector, SourceUnavailable
from app.core.collectors.orchestrator import BACKOFF_SCHEDULE_SECONDS, backoff_delay, retry_delay, run_collectors
from app.core.collectors.selection import CollectorRuntime, select_collectors
from app.models import CollectorState, Source

from .helpers import BRAND, NOW, FakeClock, memory_db


def S(**kw) -> Settings:
    return Settings(_env_file=None, database_url="sqlite://", **kw)  # type: ignore[call-arg]


# ------------------------------------------------------------------------------ selection


def test_auto_is_sample_when_nothing_live_is_configured():
    sel = select_collectors(S(), "auto", google_play_installed=lambda: False)
    assert (sel.requested_mode, sel.effective_mode, sel.names) == ("auto", "sample", ["sample"])
    assert "no live source" in sel.reason


@pytest.mark.parametrize(
    "kwargs,gp,why",
    [({"tavily_api_key": "k-123456"}, False, "Tavily key"), ({"searxng_base_url": "https://s.example.org"}, False, "SearXNG"), ({}, True, "google-play")],
)
def test_auto_is_live_when_a_live_collector_is_configured(kwargs, gp, why):
    sel = select_collectors(S(**kwargs), "auto", google_play_installed=lambda: gp)
    assert sel.effective_mode == "live" and why in sel.reason
    assert "sample" not in sel.names


def test_live_chain_order_and_reasons():
    sel = select_collectors(S(tavily_api_key="k-123456", searxng_base_url="https://s.example.org"), "live", google_play_installed=lambda: False)
    assert sel.names == ["tavily", "searxng", "itunes", "google_play"]
    by = {e.name: e for e in sel.entries}
    assert by["tavily"].configured and "Key present" in by["tavily"].reason
    assert by["searxng"].active and by["searxng"].configured
    assert not by["google_play"].configured and "not_installed" in by["google_play"].reason
    assert by["itunes"].configured


def test_live_without_key_still_lists_tavily_as_unavailable_and_skips_unset_searxng():
    sel = select_collectors(S(), "live", google_play_installed=lambda: False)
    assert sel.effective_mode == "live"
    assert sel.names == ["tavily", "itunes", "google_play"]
    assert not sel.entry("tavily").configured and "no_key" in sel.entry("tavily").reason
    assert not sel.entry("searxng").active and "not set" in sel.entry("searxng").reason


def test_sample_mode_never_includes_live_collectors_even_with_keys():
    sel = select_collectors(S(tavily_api_key="k-123456"), "sample", google_play_installed=lambda: True)
    assert sel.names == ["sample"] and sel.effective_mode == "sample"


def test_env_mapping_overrides_settings_and_bad_mode_rejected():
    sel = select_collectors(S(), "auto", env={"TAVILY_API_KEY": "from-env-123"}, google_play_installed=lambda: False)
    assert sel.effective_mode == "live" and sel.entry("tavily").configured
    with pytest.raises(ValueError):
        select_collectors(S(), "bogus")
    d = sel.as_dict()
    assert d["effective_mode"] == "live" and d["chain"][0]["name"] == "tavily" and "from-env-123" not in str(d)


def test_runtime_builds_the_chain_in_order():
    sf = memory_db()
    s = S(tavily_api_key="k-123456", searxng_base_url="https://s.example.org")
    rt = CollectorRuntime(s, sf)
    sel = select_collectors(s, "live", google_play_installed=lambda: False)
    assert [c.name for c in rt.build(sel)] == ["tavily", "searxng", "itunes", "google_play"]
    assert [c.name for c in rt.build(select_collectors(s, "sample"))] == ["sample"]


def test_settings_blank_env_values_are_unset(monkeypatch):
    monkeypatch.setenv("TAVILY_MONTHLY_BUDGET", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    s = Settings(_env_file=None, database_url="sqlite://")  # type: ignore[call-arg]
    assert s.tavily_monthly_budget == 900 and s.tavily_api_key is None
    assert s.tavily_max_queries_per_scan == 12 and s.itunes_country == "in"


def test_secret_key_is_registered_for_log_redaction():
    s = S(tavily_api_key="tvly-secret-value-123")
    assert "tvly-secret-value-123" in s.secret_values
    assert "tvly-secret-value-123" not in repr(s)


# ------------------------------------------------------------------------------ back-off


def test_backoff_schedule_and_cap():
    assert BACKOFF_SCHEDULE_SECONDS == (60, 300, 900, 3600, 21600)
    assert [backoff_delay(n) for n in (1, 2, 3, 4, 5, 6, 50)] == [60, 300, 900, 3600, 21600, 21600, 21600]
    assert backoff_delay(0) == 60


def test_retry_delay_rules():
    assert retry_delay("no_key", 3, None) is None  # local config problem: re-check next scan
    assert retry_delay("rate_limited", 1, 45) == 45  # provider told us
    assert retry_delay("rate_limited", 1, 10**9) == 24 * 3600
    assert retry_delay("unreachable", 2, None) == 300
    assert retry_delay("quota", 4, None) == 3600


# ------------------------------------------------------------------------------ orchestrator


class Fake(Collector):
    def __init__(self, name, platforms, *, results=None, error=None, boom=False, warn=None):
        super().__init__()
        self.name, self.platforms = name, tuple(platforms)
        self.results, self.error, self.boom, self.warn = results or [], error, boom, warn
        self.calls = 0

    def collect(self, brand, ctx: CollectContext):
        self.calls += 1
        self.warnings = [self.warn] if self.warn else []
        if self.boom:
            raise RuntimeError("kaboom SECRET-TOKEN-123")
        if self.error:
            raise self.error
        return [c for c in self.results if c.platform in ctx.platforms]


def cand(platform, handle, source="x", kind="social"):
    return Candidate(platform, kind, handle, handle.title(), f"https://example/{handle}", source, NOW)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def run(sf, collectors, clock=None, **kw):
    clock = clock or Clock()
    perf = kw.pop("perf", None) or FakeClock()
    return run_collectors(sf, BRAND, collectors, now=clock, perf=perf, **kw)


def test_failing_collector_does_not_stop_the_others_and_status_is_recorded():
    sf = memory_db()
    bad = Fake("tavily", ["instagram", "facebook"], error=SourceUnavailable("quota", "plan limit reached"))
    good = Fake("itunes", ["app_store"], results=[cand("app_store", "com.x.app", "itunes", "app")])
    crash = Fake("searxng", ["telegram"], boom=True)
    last = Fake("google_play", ["google_play"], results=[cand("google_play", "com.y.app", "google_play", "app")])
    res = run(sf, [bad, crash, good, last])
    assert [c.handle for c in res.candidates] == ["com.x.app", "com.y.app"]
    st = {r.collector: r for r in res.reports}
    assert st["tavily"].status == "unavailable" and st["tavily"].reason == "quota"
    assert st["searxng"].status == "unavailable" and st["searxng"].reason == "internal_error"
    assert "SECRET-TOKEN-123" not in st["searxng"].message
    assert st["itunes"].status == "online" and st["google_play"].status == "online"
    with sf() as db:
        rows = {s.platform: s for s in db.query(Source)}
    assert rows["instagram"].status == "unavailable" and "plan limit" in rows["instagram"].message
    assert rows["telegram"].status == "unavailable"
    assert rows["app_store"].status == "online" and rows["app_store"].last_sync is not None and rows["app_store"].detail == "1 apps found"
    assert rows["google_play"].status == "online"
    assert good.calls == 1 and last.calls == 1


def test_platform_is_online_when_any_covering_collector_works():
    sf = memory_db()
    down = Fake("tavily", ["app_store", "instagram"], error=SourceUnavailable("unreachable", "down"))
    up = Fake("itunes", ["app_store"], results=[])
    run(sf, [down, up])
    with sf() as db:
        rows = {s.platform: s for s in db.query(Source)}
    assert rows["app_store"].status == "online" and rows["instagram"].status == "unavailable"


def test_slow_status_above_threshold_and_partial_results_warning():
    sf = memory_db()
    perf = FakeClock()

    class Slow(Fake):
        def collect(self, brand, ctx):
            perf.t += 6.5  # 6500 ms
            return super().collect(brand, ctx)

    res = run(sf, [Slow("itunes", ["app_store"])], perf=perf, slow_threshold_ms=4000)
    assert res.reports[0].status == "slow" and res.reports[0].latency_ms == 6500
    fast = run(sf, [Fake("itunes", ["app_store"])], slow_threshold_ms=4000)
    assert fast.reports[0].status == "online"
    part = run(sf, [Fake("tavily", ["instagram"], results=[cand("instagram", "abc_def")], warn="Stopped after 2 of 6 queries")])
    assert part.reports[0].status == "slow" and part.candidates and part.partial == ["tavily"]
    with sf() as db:
        assert db.query(Source).filter_by(platform="instagram").one().status == "slow"


def test_cached_results_served_with_age_when_source_goes_down_and_backoff_applies():
    sf, clock = memory_db(), Clock()
    ok = Fake("tavily", ["instagram"], results=[cand("instagram", "scam_one", "tavily")])
    run(sf, [ok], clock)
    clock.advance(minutes=30)
    down = Fake("tavily", ["instagram"], error=SourceUnavailable("unreachable", "no route"))
    res = run(sf, [down], clock)
    r = res.reports[0]
    assert r.status == "unavailable" and r.from_cache and r.cached_age_seconds == 1800
    assert [c.handle for c in res.candidates] == ["scam_one"] and res.candidates[0].stale and res.candidates[0].cached_at == NOW
    assert r.next_retry_at == clock.now + timedelta(seconds=60) and r.consecutive_failures == 1
    with sf() as db:
        row = db.query(Source).filter_by(platform="instagram").one()
        assert row.stale_since is not None and "cached results" in row.message and row.retry_in_minutes == 1
        assert row.detail == "Cached results shown"
    # inside the back-off window the collector is NOT called again, cache still shown
    clock.advance(seconds=30)
    res = run(sf, [down], clock)
    assert down.calls == 1 and res.reports[0].in_backoff and res.candidates[0].stale
    # window over: called again, fails again, delay grows (5 min)
    clock.advance(seconds=31)
    res = run(sf, [down], clock)
    assert down.calls == 2 and res.reports[0].consecutive_failures == 2
    assert res.reports[0].next_retry_at == clock.now + timedelta(seconds=300)


def test_force_ignores_backoff_and_success_resets_counter():
    sf, clock = memory_db(), Clock()
    flaky = Fake("tavily", ["instagram"], error=SourceUnavailable("unreachable", "down"))
    run(sf, [flaky], clock)
    run(sf, [flaky], clock, force=True)
    assert flaky.calls == 2
    with sf() as db:
        assert db.query(CollectorState).one().consecutive_failures == 2
    flaky.error = None
    run(sf, [flaky], clock, force=True)
    with sf() as db:
        st = db.query(CollectorState).one()
        assert (st.consecutive_failures, st.next_retry_at, st.status) == (0, None, "online")
    clock.advance(seconds=1)
    flaky.error = SourceUnavailable("unreachable", "down again")
    res = run(sf, [flaky], clock)
    assert res.reports[0].next_retry_at == clock.now + timedelta(seconds=60), "counter restarted from the first step"


def test_provider_retry_after_is_honoured():
    sf, clock = memory_db(), Clock()
    c = Fake("tavily", ["instagram"], error=SourceUnavailable("rate_limited", "slow down", retry_after=240))
    res = run(sf, [c], clock)
    assert res.reports[0].next_retry_at == clock.now + timedelta(seconds=240)


def test_no_key_is_not_backed_off_and_is_rechecked_every_scan():
    sf, clock = memory_db(), Clock()
    c = Fake("tavily", ["instagram"], error=SourceUnavailable("no_key", "TAVILY_API_KEY is not set"))
    run(sf, [c], clock)
    run(sf, [c], clock)
    assert c.calls == 2


def test_cache_is_per_brand_and_ignored_for_other_brands():
    sf, clock = memory_db(), Clock()
    run(sf, [Fake("tavily", ["instagram"], results=[cand("instagram", "scam_one")])], clock)
    other = BrandQuery(brand_id=99, name="Other")
    down = Fake("tavily", ["instagram"], error=SourceUnavailable("unreachable", "x"))
    res = run_collectors(sf, other, [down], now=clock, perf=FakeClock())
    assert res.candidates == [] and not res.reports[0].from_cache


def test_unmonitored_platforms_are_not_queried_and_filtered():
    sf = memory_db()
    with sf() as db:
        orch.ensure_sources(db)
        for s in db.query(Source):
            s.monitored = s.platform in ("app_store",)
        db.commit()
    social = Fake("tavily", ["instagram", "facebook"], results=[cand("instagram", "a_b_c")])
    mixed = Fake("tavily2", ["instagram", "app_store"], results=[cand("instagram", "a_b_c"), cand("app_store", "com.q.r", kind="app")])
    res = run(sf, [social, mixed])
    assert social.calls == 0 and res.reports[0].status == "skipped"
    assert [c.platform for c in res.candidates] == ["app_store"]
    with sf() as db:
        assert db.query(Source).filter_by(platform="instagram").one().message == "Not checked yet."


def test_scope_limits_platforms():
    sf = memory_db()
    both = Fake("sample", ["instagram", "app_store"], results=[cand("instagram", "aaa_bbb"), cand("app_store", "com.a.b", kind="app")])
    res = run(sf, [both], scope="apps")
    assert [c.platform for c in res.candidates] == ["app_store"]
    res = run(sf, [both], only_platforms=frozenset({"instagram"}))
    assert [c.platform for c in res.candidates] == ["instagram"]


def test_candidates_from_two_collectors_are_merged_without_duplicates():
    sf = memory_db()
    a = Fake("tavily", ["instagram"], results=[cand("instagram", "dup_user")])
    b = Fake("searxng", ["instagram"], results=[cand("instagram", "dup_user"), cand("instagram", "other_user")])
    res = run(sf, [a, b])
    assert [c.handle for c in res.candidates] == ["dup_user", "other_user"]


def test_sample_chain_marks_sources_as_sample():
    sf = memory_db()
    from app.core.collectors.sample import SampleCollector

    res = run(sf, [SampleCollector()])
    assert res.candidates and all(c.data_source == "sample" for c in res.candidates)
    with sf() as db:
        assert {s.source for s in db.query(Source)} == {"sample"}


def test_reachability_enrichment_uses_the_ssrf_safe_checker_and_skips_sample():
    from app.core.pipeline.url_safety import UrlChecker
    from tests.conftest import FakeChecker

    sf = memory_db()
    fake = FakeChecker()
    live = Fake("tavily", ["instagram"], results=[cand("instagram", "live_one"), cand("instagram", "live_two")])
    res = run(sf, [live], url_checker=fake, reachability_checks=1)
    assert fake.calls == ["https://example/live_one"]
    assert res.candidates[0].extras["reachable"] is True and "reachable" not in res.candidates[1].extras
    # real UrlChecker: a private target is refused before any connection (resolver is faked, no DNS/network)
    c = Candidate("instagram", "social", "x_y_z", "X", "http://10.0.0.7/admin", "tavily", NOW)
    orch.enrich_reachability([c], UrlChecker(resolver=lambda h: ["10.0.0.7"]), 5)
    assert c.extras["reachable"] is False and "not checked" in c.extras["reachability_message"]
    smp = Candidate("instagram", "social", "s_s_s", "S", "https://example/s", "sample", NOW)
    fake2 = FakeChecker()
    orch.enrich_reachability([smp], fake2, 5)
    assert fake2.calls == []


def test_naive_datetimes_from_sqlite_are_handled():
    assert orch._age(datetime(2026, 10, 13, 12, 5, tzinfo=UTC), datetime(2026, 10, 13, 12, 0)) == 300

"""Data mode (live | sample | auto) and the ordered collector chain, with the reason for every entry."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.config.config import Settings
from app.config.time import utcnow
from app.core.collectors.base import Collector, default_client_factory
from app.core.collectors.credits import CreditLedger
from app.core.collectors.google_play import GooglePlayCollector, package_installed
from app.core.collectors.itunes import ITunesCollector
from app.core.collectors.sample import SampleCollector
from app.core.collectors.searxng import SearxngCollector
from app.core.collectors.tavily import TavilyCollector
from app.core.collectors.util import Clock, Sleep, SpacingLimiter, TTLCache
from app.core.providers.safe_browsing import DailyLedger, SafeBrowsingClient

DATA_MODES = ("live", "sample", "auto")
CHAIN_ORDER = ("tavily", "searxng", "itunes", "google_play")


@dataclass
class ChainEntry:
    name: str
    active: bool  # will be run in this mode
    configured: bool  # has what it needs (key / package / base url)
    reason: str


@dataclass
class Selection:
    requested_mode: str
    effective_mode: str  # live | sample
    reason: str
    entries: list[ChainEntry] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [e.name for e in self.entries if e.active]

    def entry(self, name: str) -> ChainEntry | None:
        return next((e for e in self.entries if e.name == name), None)

    def as_dict(self) -> dict:
        return {
            "data_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "reason": self.reason,
            "chain": [{"name": e.name, "active": e.active, "configured": e.configured, "reason": e.reason} for e in self.entries],
        }


def _cfg(settings: Settings, env: Mapping[str, str] | None, env_name: str, attr: str) -> Any:
    if env is not None and (env.get(env_name) or "").strip():
        return env[env_name].strip()
    v = getattr(settings, attr, None)
    return v.get_secret_value() if hasattr(v, "get_secret_value") else v


def select_collectors(
    settings: Settings,
    data_mode: str = "auto",
    *,
    env: Mapping[str, str] | None = None,
    google_play_installed: Callable[[], bool] = package_installed,
) -> Selection:
    """Choose the chain. `env` (optional) overrides TAVILY_API_KEY / SEARXNG_BASE_URL from the Settings object.

    auto -> live when at least one opt-in live collector is configured (Tavily key, SearXNG base URL or the
    optional google-play package); otherwise sample. iTunes needs no key, so on its own it does not switch
    auto mode to live (a fresh install must not start querying real stores for a demo brand).
    """
    if data_mode not in DATA_MODES:
        raise ValueError(f"data_mode must be one of {DATA_MODES}")
    key = _cfg(settings, env, "TAVILY_API_KEY", "tavily_api_key")
    base = _cfg(settings, env, "SEARXNG_BASE_URL", "searxng_base_url")
    gp = bool(google_play_installed())
    has_key, has_base = bool(key), bool(base)

    if data_mode == "sample":
        effective, why = "sample", "Data mode is 'sample'."
    elif data_mode == "live":
        effective, why = "live", "Data mode is 'live'."
    elif has_key or has_base or gp:
        effective = "live"
        parts = [n for n, ok in (("Tavily key", has_key), ("SearXNG base URL", has_base), ("google-play package", gp)) if ok]
        why = "Auto: live because configured: " + ", ".join(parts) + "."
    else:
        effective, why = "sample", "Auto: sample because no live source is configured (no TAVILY_API_KEY, SEARXNG_BASE_URL or google-play package)."

    if effective == "sample":
        return Selection(data_mode, "sample", why, [ChainEntry("sample", True, True, "Sample data for the fictional/user brand; never real results.")])

    entries = [
        ChainEntry("tavily", True, has_key, "Key present." if has_key else "No TAVILY_API_KEY: reports 'unavailable (no_key)'."),
        ChainEntry(
            "searxng", has_base, has_base,
            "SEARXNG_BASE_URL set (optional fallback)." if has_base else "SEARXNG_BASE_URL not set: optional fallback skipped.",
        ),
        ChainEntry("itunes", True, True, "No key needed (about 20 calls/min, cached)."),
        ChainEntry(
            "google_play", True, gp,
            "Optional google-play-scraper package installed." if gp else "Package not installed: reports 'unavailable (not_installed)'.",
        ),
    ]
    return Selection(data_mode, "live", why, entries)


class CollectorRuntime:
    """Long-lived state shared by scans: iTunes cache + rate limiter, Play limiter, HTTP transport, clocks."""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.transport = transport
        self.now = now
        self.itunes_limiter = SpacingLimiter(settings.itunes_calls_per_minute, clock=clock, sleep=sleep)
        self.itunes_cache = TTLCache(settings.itunes_cache_ttl_seconds, clock=clock)
        self.play_limiter = SpacingLimiter(30, clock=clock, sleep=sleep)
        self.ledger = CreditLedger(session_factory, settings.tavily_monthly_budget, now=now)
        # URL reputation (Google Safe Browsing). Key only from the environment; without it every lookup is "unknown".
        key = settings.google_safe_browsing_api_key.get_secret_value() if settings.google_safe_browsing_api_key else None
        self.safe_browsing = SafeBrowsingClient(
            key, client_factory=self.client_factory_for(settings.safe_browsing_timeout_seconds),
            ledger=DailyLedger(session_factory, settings.safe_browsing_daily_budget, now=now),
            cache=TTLCache(settings.safe_browsing_cache_ttl_seconds, clock=clock), now=now, timeout=settings.safe_browsing_timeout_seconds,
        )

    def client_factory_for(self, timeout: float) -> Callable[[], httpx.Client]:
        if self.transport is None:
            return default_client_factory(timeout)
        transport = self.transport
        return lambda: httpx.Client(timeout=timeout, transport=transport)

    def client_factory(self) -> Callable[[], httpx.Client]:
        return self.client_factory_for(self.settings.collector_timeout_seconds)

    def build(self, selection: Selection, *, env: Mapping[str, str] | None = None, google_play_search: Callable | None = None) -> list[Collector]:
        s = self.settings
        out: list[Collector] = []
        for e in selection.entries:
            if not e.active:
                continue
            if e.name == "sample":
                out.append(SampleCollector())
            elif e.name == "tavily":
                out.append(
                    TavilyCollector(
                        _cfg(s, env, "TAVILY_API_KEY", "tavily_api_key"), self.ledger, client_factory=self.client_factory(),
                        max_queries_per_scan=s.tavily_max_queries_per_scan, search_depth=s.tavily_search_depth, max_results=s.tavily_max_results,
                    )
                )
            elif e.name == "searxng":
                out.append(
                    SearxngCollector(
                        _cfg(s, env, "SEARXNG_BASE_URL", "searxng_base_url"), client_factory=self.client_factory(),
                        allow_private_hosts=s.searxng_allow_private_hosts, max_queries_per_scan=s.searxng_max_queries_per_scan,
                    )
                )
            elif e.name == "itunes":
                out.append(
                    ITunesCollector(
                        client_factory=self.client_factory(), limiter=self.itunes_limiter, cache=self.itunes_cache,
                        country=s.itunes_country, max_queries_per_scan=s.itunes_max_queries_per_scan,
                    )
                )
            elif e.name == "google_play":
                out.append(
                    GooglePlayCollector(
                        search_fn=google_play_search, limiter=self.play_limiter, country=s.google_play_country,
                        max_queries_per_scan=s.google_play_max_queries_per_scan,
                    )
                )
        return out

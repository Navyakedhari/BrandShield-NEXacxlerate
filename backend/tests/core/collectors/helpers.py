"""Shared test helpers for collector tests. No live network: everything goes through httpx.MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import sessionmaker

from app.core.collectors.base import BrandQuery, CollectContext
from app.core.collectors.credits import CreditLedger
from app.core.collectors.google_play import GooglePlayCollector
from app.core.collectors.itunes import ITunesCollector
from app.core.collectors.sample import SampleCollector
from app.core.collectors.searxng import SearxngCollector
from app.core.collectors.tavily import TavilyCollector
from app.core.collectors.util import SpacingLimiter, TTLCache
from app.db.base import Base
from app.db.session import make_engine, make_session_factory

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
TEST_KEY = "tvly-test-key-not-real-0123456789"

BRAND = BrandQuery(
    brand_id=1,
    name="Acme Bank",
    aliases=("Acme",),
    keywords=("acmebank",),
    official_handles={"instagram": ("acmebank",), "x": ("acmebank",)},
    official_apps=(("Acme Bank", "com.acmebank.mobile", "Acme Bank Ltd"),),
)


def fixture_text(rel: str) -> str:
    return (FIXTURES / rel).read_text(encoding="utf-8")


def fixture_json(rel: str):
    return json.loads(fixture_text(rel))


def ctx(**kw) -> CollectContext:
    kw.setdefault("now", lambda: NOW)
    return CollectContext(**kw)


def client_factory(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[[], httpx.Client]:
    transport = httpx.MockTransport(handler)
    return lambda: httpx.Client(transport=transport)


def respond(status: int = 200, *, body: str | None = None, fixture: str | None = None, headers: dict | None = None):
    text = body if body is not None else (fixture_text(fixture) if fixture else "{}")
    ct = "application/json" if (fixture or "").endswith(".json") or body is None else "text/plain"
    return httpx.Response(status, content=text.encode("utf-8"), headers={"content-type": ct, **(headers or {})})


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def memory_db():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def make_ledger(sf: sessionmaker | None = None, budget: int = 900, now: Callable[[], datetime] = lambda: NOW) -> CreditLedger:
    return CreditLedger(sf or memory_db(), budget, now=now)


def make_tavily(handler, *, ledger: CreditLedger | None = None, key: str | None = TEST_KEY, **kw) -> TavilyCollector:
    return TavilyCollector(key, ledger or make_ledger(), client_factory=client_factory(handler), **kw)


def make_searxng(handler, base: str | None = "https://searx.example.org", **kw) -> SearxngCollector:
    kw.setdefault("resolver", lambda host: ["93.184.216.34"])
    return SearxngCollector(base, client_factory=client_factory(handler), **kw)


def make_itunes(handler, clock: FakeClock | None = None, **kw) -> ITunesCollector:
    clock = clock or FakeClock()
    kw.setdefault("limiter", SpacingLimiter(20, clock=clock, sleep=clock.sleep))
    kw.setdefault("cache", TTLCache(3600, clock=clock))
    return ITunesCollector(client_factory=client_factory(handler), **kw)


def make_play(search_fn, **kw) -> GooglePlayCollector:
    return GooglePlayCollector(search_fn=search_fn, **kw)


def make_sample(**kw) -> SampleCollector:
    return SampleCollector(**kw)

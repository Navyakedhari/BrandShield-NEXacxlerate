"""Helpers for scan / detection API tests. No live network: collectors are fakes or the sample collector."""

from __future__ import annotations

import io
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from PIL import Image

from app.core.collectors.base import BrandQuery, Candidate, CollectContext, Collector, SourceUnavailable
from app.core.collectors.sample import SampleCollector
from app.core.collectors.selection import CollectorRuntime
from app.db.base import Base
from app.db.session import make_engine, make_session_factory
from app.main import create_app

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


class StaticCollector(Collector):
    """Returns exactly the candidates it was given (a 'live' collector: its name is not 'sample')."""

    name = "static"
    kind = "mixed"
    platforms = ("instagram", "facebook", "x", "telegram", "linkedin", "google_play", "app_store", "third_party_apk", "web")

    def __init__(self, candidates: list[Candidate]) -> None:
        super().__init__()
        self.candidates = candidates

    def collect(self, brand: BrandQuery, ctx: CollectContext) -> list[Candidate]:
        return [c for c in self.candidates if c.platform in ctx.platforms]


class FailingCollector(Collector):
    name = "failing"
    kind = "mixed"
    platforms = ("instagram", "facebook", "x", "telegram", "linkedin")

    def collect(self, brand: BrandQuery, ctx: CollectContext) -> list[Candidate]:
        raise SourceUnavailable("unreachable", "The failing test source cannot be reached.")


class FixedRuntime(CollectorRuntime):
    """Collector runtime whose chain is fixed by the test."""

    def __init__(self, settings, session_factory, collectors, transport=None) -> None:
        super().__init__(settings, session_factory, transport=transport, now=lambda: datetime.now(UTC))
        self.fixed = collectors

    def build(self, selection, **_kw):
        return list(self.fixed)


def social(platform: str, handle: str, name: str | None = None, *, bio: str | None = None, source: str = "static", **kw) -> Candidate:
    urls = {"instagram": "https://www.instagram.com/{}/", "facebook": "https://www.facebook.com/{}", "x": "https://x.com/{}",
            "telegram": "https://t.me/{}", "linkedin": "https://www.linkedin.com/company/{}"}
    return Candidate(platform=platform, kind="social", handle=handle, display_name=name or handle, profile_url=urls[platform].format(handle),
                     source=source, bio=bio, **kw)


def app_candidate(platform: str, package: str, name: str, publisher: str = "Quick Tools Studio", source: str = "static", **kw) -> Candidate:
    store = {"google_play": "google_play", "app_store": "apple_app_store", "third_party_apk": "apkpure"}[platform]
    url = {"google_play": f"https://play.google.com/store/apps/details?id={package}"}.get(platform, f"https://apkpure.com/x/{package}")
    return Candidate(platform=platform, kind="app", handle=package, display_name=name, profile_url=url, source=source, store=store,
                     publisher=publisher, **kw)


def web(url: str, source: str = "static", **kw) -> Candidate:
    """A website candidate through the same constructor the collectors use."""
    from app.core.collectors.base import candidate_from_web_url

    c = candidate_from_web_url(url, source=source, **kw)
    assert c is not None, url
    return c


def png_bytes(color=(200, 30, 30), size=300, shape="square") -> bytes:
    img = Image.new("RGB", (size, size), (255, 255, 255))
    px = img.load()
    for x in range(size // 4, 3 * size // 4):
        for y in range(size // 4, 3 * size // 4):
            if shape == "square" or (x - size // 2) ** 2 + (y - size // 2) ** 2 < (size // 4) ** 2:
                px[x, y] = color
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@contextmanager
def make_client(settings, checker, collectors=None, *, transport=None, **kw):
    """A TestClient on a fresh in-memory DB. With `collectors`, the scan chain is exactly those collectors."""
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    runtime = FixedRuntime(settings, sf, collectors, transport) if collectors is not None else None
    app = create_app(settings, engine=engine, url_checker=checker, collector_runtime=runtime, **kw)
    with TestClient(app) as c:
        c.app_ref = app  # type: ignore[attr-defined]
        yield c


def set_mode(client, headers, mode: str) -> None:
    r = client.put("/api/settings/data-mode", json={"data_mode": mode}, headers=headers)
    assert r.status_code == 200, r.text


def setup_brand(client, headers, *, accounts=(("instagram", "acmebank"), ("x", "acmebank")), apps=(("Acme Bank Mobile", "com.acmebank.mobile", "Acme Bank Ltd"),)) -> dict:
    b = client.post("/api/brands", json={"name": "Acme Bank", "aliases": ["Acme"], "keywords": ["acmebank"], "domains": ["acmebank.com"]}, headers=headers)
    assert b.status_code == 201, b.text
    bid = b.json()["id"]
    for platform, handle in accounts:
        assert client.post(f"/api/brands/{bid}/accounts", json={"platform": platform, "handle": handle}, headers=headers).status_code == 201
    for name, pkg, pub in apps:
        r = client.post(f"/api/brands/{bid}/apps", json={"name": name, "package_id": pkg, "publisher": pub, "stores": ["google_play"]}, headers=headers)
        assert r.status_code == 201, r.text
    return b.json()


def run_scan(client, headers, scope: str = "all") -> dict:
    r = client.post("/api/scan", json={"scope": scope}, headers=headers)
    assert r.status_code == 202, r.text
    return r.json()["scan"]


def all_detections(client, headers, **params) -> list[dict]:
    r = client.get("/api/detections", params={"limit": 200, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


__all__ = ["FailingCollector", "FixedRuntime", "SampleCollector", "StaticCollector", "all_detections", "app_candidate", "make_client", "png_bytes",
           "run_scan", "set_mode", "setup_brand", "social", "web"]

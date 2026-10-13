"""Google Play collector behind an adapter around an OPTIONAL third-party package.

Google has no public search API for Play. The adapter lazily imports `google_play_scraper` (PyPI:
google-play-scraper), which scrapes Play pages. It is NOT in requirements.txt. When the package is absent
the collector reports SourceUnavailable("not_installed"). The package's license and terms-of-use risk are
UNVERIFIED (could not be checked offline); see docs/DEPENDENCIES.md before installing it.

Tests inject `search_fn`, so the package is never needed to run them.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable
from typing import Any

from app.core.collectors.base import (
    BrandQuery,
    Candidate,
    CollectContext,
    Collector,
    SourceUnavailable,
    as_float,
    as_int,
    as_text,
    dedupe,
)
from app.core.collectors.util import SpacingLimiter
from app.core.collectors.variants import generate_variants
from app.core.engine.urls import parse_store_url

PACKAGE = "google_play_scraper"
SearchFn = Callable[..., Any]


def package_installed() -> bool:
    try:
        return importlib.util.find_spec(PACKAGE) is not None
    except (ImportError, ValueError):
        return False


def _load_search() -> SearchFn:
    try:
        mod = importlib.import_module(PACKAGE)
    except ImportError as exc:
        raise SourceUnavailable(
            "not_installed",
            "The optional google-play-scraper package is not installed, so Google Play is not searched. "
            "Install it only after reviewing its license (docs/DEPENDENCIES.md).",
        ) from exc
    fn = getattr(mod, "search", None)
    if not callable(fn):
        raise SourceUnavailable("not_installed", "The installed google-play-scraper package has no search() function.")
    return fn


def build_terms(brand: BrandQuery) -> list[str]:
    terms = brand.terms
    out = list(terms)
    if terms:
        out.extend(generate_variants(terms[0], limit=2))
        out.extend(f"{terms[0]} {w}" for w in ("support", "rewards"))
    return list(dict.fromkeys(out))


class GooglePlayCollector(Collector):
    name = "google_play"
    kind = "app"
    platforms = ("google_play",)

    def __init__(
        self,
        *,
        search_fn: SearchFn | None = None,
        limiter: SpacingLimiter | None = None,
        country: str = "in",
        lang: str = "en",
        n_hits: int = 20,
        max_queries_per_scan: int = 6,
        installed: Callable[[], bool] = package_installed,
    ) -> None:
        super().__init__()
        self._search_fn = search_fn
        self.limiter = limiter
        self.country = (country or "in").lower()[:2]
        self.lang = lang
        self.n_hits = n_hits
        self.max_queries = max(0, max_queries_per_scan)
        self._installed = installed

    def is_configured(self) -> bool:
        return self._search_fn is not None or self._installed()

    def collect(self, brand: BrandQuery, ctx: CollectContext) -> list[Candidate]:
        self.warnings = []
        if "google_play" not in ctx.platforms:
            return []
        search = self._search_fn or _load_search()
        cap = self.max_queries if ctx.max_queries is None else min(self.max_queries, ctx.max_queries)
        terms = build_terms(brand)[:cap]
        if not terms:
            raise SourceUnavailable("not_configured", "Google Play has no search terms: the per-scan cap is 0 or the brand has no name.")
        out: list[Candidate] = []
        done = 0
        for term in terms:
            if self.limiter:
                self.limiter.wait()
            try:
                results = search(term, lang=self.lang, country=self.country, n_hits=self.n_hits)
            except Exception as exc:  # noqa: BLE001 - third-party library: any failure means "unavailable", never a crash
                if done == 0:
                    raise SourceUnavailable("unreachable", f"Google Play search failed ({type(exc).__name__}).") from exc
                self.warnings.append(f"Stopped after {done} of {len(terms)} searches ({type(exc).__name__}).")
                break
            done += 1
            if not isinstance(results, list):
                continue
            out.extend(c for r in results if isinstance(r, dict) and (c := self._normalize(r, ctx)) is not None)
        return dedupe(out)

    def _normalize(self, r: dict, ctx: CollectContext) -> Candidate | None:
        pkg = as_text(r.get("appId"), 200)
        name = as_text(r.get("title"), 200)
        if not pkg:
            return None  # without a package id the listing cannot be compared with the allowlist
        url = as_text(r.get("url"), 500)
        parsed = parse_store_url(url) if url else None
        canonical = parsed.canonical_url if parsed and parsed.ok else f"https://play.google.com/store/apps/details?id={pkg}"
        extras: dict = {}
        installs = as_text(r.get("installs"), 40)
        if installs:
            extras["installs"] = installs
        return Candidate(
            platform="google_play",
            kind="app",
            handle=pkg,
            display_name=name,
            profile_url=canonical,
            source=self.name,
            fetched_at=ctx.now(),
            bio=as_text(r.get("description")),
            title=name,
            store="google_play",
            publisher=as_text(r.get("developer"), 200),
            icon_url=as_text(r.get("icon"), 500),
            rating=as_float(r.get("score")),
            rating_count=as_int(r.get("ratings")),
            extras=extras,
        )

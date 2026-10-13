"""Apple iTunes Search collector (App Store listings, no key needed).

API (public docs): GET https://itunes.apple.com/search?term=&country=&entity=software&limit=
-> {"resultCount": n, "results": [{trackName, bundleId, sellerName, artworkUrl512, trackViewUrl, description,
averageUserRating, userRatingCount, releaseDate, version, trackId ...}]}.
Apple documents roughly 20 calls per minute per IP: calls are spaced (SpacingLimiter) and answers are cached
with a TTL, so the same term is not asked twice within the TTL. Every field may be absent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import httpx

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
    json_object,
    parse_retry_after,
)
from app.core.collectors.util import SpacingLimiter, TTLCache
from app.core.collectors.variants import generate_variants
from app.core.engine.urls import parse_store_url

ENDPOINT = "https://itunes.apple.com/search"


def build_terms(brand: BrandQuery) -> list[str]:
    terms = brand.terms
    out = list(terms)
    for t in terms[:1]:
        out.extend(generate_variants(t, limit=3))
        out.extend(f"{t} {w}" for w in ("support", "rewards"))
    return list(dict.fromkeys(out))


def _parse_date(value: object) -> datetime | None:
    s = as_text(value, 40)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class ITunesCollector(Collector):
    name = "itunes"
    kind = "app"
    platforms = ("app_store",)

    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.Client],
        limiter: SpacingLimiter,
        cache: TTLCache,
        country: str = "in",
        limit: int = 25,
        max_queries_per_scan: int = 8,
        endpoint: str = ENDPOINT,
    ) -> None:
        super().__init__()
        self._client_factory = client_factory
        self.limiter = limiter
        self.cache = cache
        self.country = (country or "in").strip().lower()[:2] or "in"
        self.limit = max(1, min(limit, 200))
        self.max_queries = max(0, max_queries_per_scan)
        self.endpoint = endpoint

    def collect(self, brand: BrandQuery, ctx: CollectContext) -> list[Candidate]:
        self.warnings = []
        if "app_store" not in ctx.platforms:
            return []
        cap = self.max_queries if ctx.max_queries is None else min(self.max_queries, ctx.max_queries)
        terms = build_terms(brand)[:cap]
        if not terms:
            raise SourceUnavailable("not_configured", "iTunes has no search terms: the per-scan cap is 0 or the brand has no name.")
        out: list[Candidate] = []
        done = 0
        try:
            with self._client_factory() as client:
                for term in terms:
                    try:
                        out.extend(self._one(client, term, ctx))
                        done += 1
                    except SourceUnavailable as exc:
                        if done == 0:
                            raise
                        self.warnings.append(f"Stopped after {done} of {len(terms)} searches: {exc.message}")
                        break
        except SourceUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise SourceUnavailable("unreachable", "The App Store search could not be reached.") from exc
        return dedupe(out)

    def _one(self, client: httpx.Client, term: str, ctx: CollectContext) -> list[Candidate]:
        key = (term.lower(), self.country, self.limit)
        data = self.cache.get(key)
        if data is None:
            self.limiter.wait()
            try:
                resp = client.get(
                    self.endpoint, params={"term": term, "country": self.country, "entity": "software", "limit": self.limit}
                )
            except httpx.TimeoutException as exc:
                raise SourceUnavailable("timeout", "The App Store search did not answer in time.") from exc
            except httpx.HTTPError as exc:
                raise SourceUnavailable("unreachable", "The App Store search could not be reached.") from exc
            if resp.status_code in (403, 429):
                raise SourceUnavailable(
                    "rate_limited",
                    f"The App Store search is rate limiting requests ({resp.status_code}).",
                    parse_retry_after(resp.headers.get("retry-after")) or 60.0,
                )
            if resp.status_code >= 500:
                raise SourceUnavailable("unreachable", f"The App Store search had a server problem ({resp.status_code}).")
            if resp.status_code != 200:
                raise SourceUnavailable("unexpected_status", f"The App Store search answered with HTTP {resp.status_code}.")
            data = json_object(resp, "The App Store search")
            self.cache.set(key, data)
        results = data.get("results")
        if not isinstance(results, list):
            return []
        return [c for r in results if isinstance(r, dict) and (c := self._normalize(r, ctx)) is not None]

    def _normalize(self, r: dict, ctx: CollectContext) -> Candidate | None:
        name = as_text(r.get("trackName"), 200)
        bundle = as_text(r.get("bundleId"), 200)
        track_id = as_int(r.get("trackId"))
        if not (name or bundle or track_id):
            return None
        raw_url = as_text(r.get("trackViewUrl"), 500)
        url = None
        if raw_url:
            parsed = parse_store_url(raw_url)
            url = parsed.canonical_url if parsed.ok else None
        if url is None and track_id:
            url = f"https://apps.apple.com/app/id{track_id}"
        icon = as_text(r.get("artworkUrl512") or r.get("artworkUrl100") or r.get("artworkUrl60"), 500)
        extras: dict = {}
        if track_id:
            extras["apple_app_id"] = str(track_id)
        for src, dst in (("version", "version"), ("primaryGenreName", "genre")):
            v = as_text(r.get(src), 80)
            if v:
                extras[dst] = v
        released = _parse_date(r.get("releaseDate"))
        if released:
            extras["released_at"] = released.isoformat()
        return Candidate(
            platform="app_store",
            kind="app",
            handle=bundle or (str(track_id) if track_id else None),
            display_name=name,
            profile_url=url,
            source=self.name,
            fetched_at=ctx.now(),
            bio=as_text(r.get("description")),
            title=name,
            store="apple_app_store",
            publisher=as_text(r.get("sellerName"), 200),
            icon_url=icon,
            rating=as_float(r.get("averageUserRating")),
            rating_count=as_int(r.get("userRatingCount")),
            extras=extras,
        )

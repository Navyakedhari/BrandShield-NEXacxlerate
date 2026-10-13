"""SearXNG collector (optional fallback for social profiles; needs a self-hosted/operator instance).

API: GET {base}/search?q=...&format=json -> {"results":[{"title","url","content","engine",...}], ...}.
The instance must allow the json format (settings.yml: `search: formats: [html, json]`); otherwise it
answers 403.

SSRF: the base URL is operator configuration, not user input. By default it must resolve to a public
address (reusing services/url_safety.validate_public_url). Operators running SearXNG on a private network
set SEARXNG_ALLOW_PRIVATE_HOSTS=true to accept private hosts (only http/https is still enforced).
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from app.core.collectors.base import (
    BrandQuery,
    Candidate,
    CollectContext,
    Collector,
    SourceUnavailable,
    candidate_from_url,
    dedupe,
    json_object,
    parse_retry_after,
)
from app.core.collectors.tavily import LURE_WORDS
from app.core.collectors.variants import generate_variants
from app.core.pipeline.url_safety import Resolver, UnsafeURLError, system_resolver, validate_public_url

JSON_DISABLED_HELP = (
    "The SearXNG instance refused format=json (HTTP 403). Enable it in the instance settings.yml: "
    "search: formats: [html, json] - then restart SearXNG."
)


def build_queries(brand: BrandQuery) -> list[str]:
    terms = brand.terms
    out: list[str] = []
    if not terms:
        return out
    for w in LURE_WORDS:
        out.append(f'"{terms[0]}" {w}')
    for t in terms[1:]:
        out.append(f'"{t}" official')
    for v in generate_variants(terms[0], limit=3):
        out.append(f'"{v}"')
    return list(dict.fromkeys(out))


class SearxngCollector(Collector):
    name = "searxng"
    kind = "social"
    platforms = ("instagram", "facebook", "x", "telegram", "linkedin")

    def __init__(
        self,
        base_url: str | None,
        *,
        client_factory: Callable[[], httpx.Client],
        allow_private_hosts: bool = False,
        max_queries_per_scan: int = 12,
        resolver: Resolver = system_resolver,
    ) -> None:
        super().__init__()
        self.base_url = (base_url or "").strip().rstrip("/") or None
        self._client_factory = client_factory
        self.allow_private_hosts = allow_private_hosts
        self.max_queries = max(0, max_queries_per_scan)
        self._resolver = resolver

    def is_configured(self) -> bool:
        return self.base_url is not None

    def _check_base(self) -> str:
        if not self.base_url:
            raise SourceUnavailable("not_configured", "SEARXNG_BASE_URL is not set.")
        if self.allow_private_hosts:
            parts = urlsplit(self.base_url)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
                raise SourceUnavailable("unsafe_base_url", "SEARXNG_BASE_URL must be an http(s) URL without credentials.")
            return self.base_url
        try:
            validate_public_url(self.base_url, self._resolver)
        except UnsafeURLError as exc:
            raise SourceUnavailable(
                "unsafe_base_url",
                f"SEARXNG_BASE_URL was refused: {exc}. If the instance is on a private network set SEARXNG_ALLOW_PRIVATE_HOSTS=true.",
            ) from exc
        return self.base_url

    def collect(self, brand: BrandQuery, ctx: CollectContext) -> list[Candidate]:
        self.warnings = []
        base = self._check_base()
        cap = self.max_queries if ctx.max_queries is None else min(self.max_queries, ctx.max_queries)
        queries = build_queries(brand)[:cap]
        if not queries:
            raise SourceUnavailable("not_configured", "SearXNG has no queries to run: the per-scan cap is 0 or the brand has no name.")
        allowed = frozenset(ctx.platforms) & frozenset(self.platforms)
        out: list[Candidate] = []
        done = 0
        try:
            with self._client_factory() as client:
                for q in queries:
                    try:
                        out.extend(self._one(client, base, q, allowed, ctx))
                        done += 1
                    except SourceUnavailable as exc:
                        if done == 0:
                            raise
                        self.warnings.append(f"Stopped after {done} of {len(queries)} queries: {exc.message}")
                        break
        except SourceUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise SourceUnavailable("unreachable", "SearXNG could not be reached.") from exc
        return dedupe(out)

    def _one(self, client: httpx.Client, base: str, query: str, allowed: frozenset[str], ctx: CollectContext) -> list[Candidate]:
        try:
            resp = client.get(f"{base}/search", params={"q": query, "format": "json"}, headers={"Accept": "application/json"})
        except httpx.TimeoutException as exc:
            raise SourceUnavailable("timeout", "SearXNG did not answer in time.") from exc
        except httpx.HTTPError as exc:
            raise SourceUnavailable("unreachable", "SearXNG could not be reached.") from exc
        if resp.status_code == 403:
            raise SourceUnavailable("json_disabled", JSON_DISABLED_HELP)
        if resp.status_code == 429:
            raise SourceUnavailable("rate_limited", "SearXNG is rate limiting requests (429).", parse_retry_after(resp.headers.get("retry-after")))
        if resp.status_code >= 500:
            raise SourceUnavailable("unreachable", f"SearXNG had a server problem ({resp.status_code}).")
        if resp.status_code != 200:
            raise SourceUnavailable("unexpected_status", f"SearXNG answered with HTTP {resp.status_code}.")
        data = json_object(resp, "SearXNG")
        results = data.get("results")
        if not isinstance(results, list):
            return []
        out: list[Candidate] = []
        for r in results:
            if not isinstance(r, dict):
                continue
            url, title, content, engine = r.get("url"), r.get("title"), r.get("content"), r.get("engine")
            c = candidate_from_url(
                url if isinstance(url, str) else None,
                source=self.name,
                title=title if isinstance(title, str) else None,
                snippet=content if isinstance(content, str) else None,
                fetched_at=ctx.now(),
                extras={"engine": engine if isinstance(engine, str) else None, "query": query},
                allowed_platforms=allowed,
            )
            if c is not None:
                out.append(c)
        return out

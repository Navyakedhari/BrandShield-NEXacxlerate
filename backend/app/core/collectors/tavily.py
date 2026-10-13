"""Tavily Search collector (primary live source for social profiles, pages and store listings).

API (public docs): POST https://api.tavily.com/search, `Authorization: Bearer <key>`, JSON body with
query, search_depth, max_results, include_domains, exact_match, include_usage ...; response has
results[{title,url,content,score}], usage{credits}, request_id, response_time.
Credits per call: basic = 1, advanced = 2. Errors: 401 bad key, 429 rate limit (Retry-After),
432 plan limit, 433 pay-as-you-go limit, 5xx upstream.

Every call is guarded by a persistent monthly credit counter (credits.py). The key is read from the
environment by the caller and is never logged or put in a message.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from app.core.collectors.base import (
    BrandQuery,
    Candidate,
    CollectContext,
    Collector,
    SourceUnavailable,
    as_float,
    as_int,
    candidate_from_url,
    dedupe,
    json_object,
    parse_retry_after,
)
from app.core.collectors.credits import CreditLedger, credit_cost
from app.core.collectors.variants import generate_variants

log = logging.getLogger("app.core.collectors.tavily")

ENDPOINT = "https://api.tavily.com/search"

# Domain restriction: social platforms and app stores only.
PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com",),
    "x": ("x.com", "twitter.com"),
    "telegram": ("t.me",),
    "linkedin": ("linkedin.com",),
    "youtube": ("youtube.com",),
    "google_play": ("play.google.com",),
    "app_store": ("apps.apple.com",),
    "third_party_apk": ("apkpure.com", "apkcombo.com", "apkmirror.com", "uptodown.com", "aptoide.com", "apkmonk.com"),
}
LURE_WORDS = ("support", "official", "helpline", "rewards")


def build_queries(brand: BrandQuery, *, words: tuple[str, ...] = LURE_WORDS, variants_per_term: int = 4) -> list[str]:
    """Quoted queries in priority order: brand x lure words, aliases/keywords, then look-alike variants."""
    terms = brand.terms
    if not terms:
        return []
    queries: list[str] = []

    def add(q: str) -> None:
        if q not in queries:
            queries.append(q)

    primary = terms[0]
    for w in words:
        add(f'"{primary}" {w}')
    for t in terms[1:]:
        add(f'"{t}" official')
        add(f'"{t}" support')
    for t in terms[:2]:
        for v in generate_variants(t, limit=variants_per_term):
            add(f'"{v}"')
    for t in terms[1:]:
        for w in words[2:]:
            add(f'"{t}" {w}')
    return queries


class TavilyCollector(Collector):
    name = "tavily"
    kind = "mixed"
    platforms = ("instagram", "facebook", "x", "telegram", "linkedin", "google_play", "app_store", "third_party_apk")

    def __init__(
        self,
        api_key: str | None,
        ledger: CreditLedger,
        *,
        client_factory: Callable[[], httpx.Client],
        max_queries_per_scan: int = 12,
        search_depth: str = "basic",
        max_results: int = 10,
        endpoint: str = ENDPOINT,
    ) -> None:
        super().__init__()
        self._key = (api_key or "").strip() or None
        self.ledger = ledger
        self._client_factory = client_factory
        self.max_queries = max(0, max_queries_per_scan)
        self.search_depth = search_depth if search_depth in ("basic", "advanced") else "basic"
        self.max_results = max(1, min(max_results, 20))
        self.endpoint = endpoint
        self.cost = credit_cost(self.search_depth)

    def is_configured(self) -> bool:
        return self._key is not None

    # ------------------------------------------------------------------
    def collect(self, brand: BrandQuery, ctx: CollectContext) -> list[Candidate]:
        self.warnings = []
        if not self._key:
            raise SourceUnavailable("no_key", "TAVILY_API_KEY is not set. Add it to server/.env to enable Tavily search.")
        allowed = frozenset(p for p in ctx.platforms if p in PLATFORM_DOMAINS) | {"youtube"}
        domains = [d for p, ds in PLATFORM_DOMAINS.items() if p in allowed for d in ds]
        cap = self.max_queries if ctx.max_queries is None else min(self.max_queries, ctx.max_queries)
        queries = build_queries(brand)[:cap]
        if not queries:
            raise SourceUnavailable("not_configured", "Tavily has no queries to run: the per-scan cap is 0 or the brand has no name.")
        out: list[Candidate] = []
        done = 0
        try:
            with self._client_factory() as client:
                for q in queries:
                    try:
                        out.extend(self._one_query(client, q, domains, allowed, ctx))
                        done += 1
                    except SourceUnavailable as exc:
                        if done == 0:
                            raise
                        self.warnings.append(f"Stopped after {done} of {len(queries)} queries: {exc.message}")
                        break
        except SourceUnavailable:
            raise
        except httpx.HTTPError as exc:  # client construction / unexpected transport error
            raise SourceUnavailable("unreachable", "Tavily could not be reached.") from exc
        return dedupe(out)

    def _one_query(
        self, client: httpx.Client, query: str, domains: list[str], allowed: frozenset[str], ctx: CollectContext
    ) -> list[Candidate]:
        if not self.ledger.reserve(self.cost):
            snap = self.ledger.snapshot()
            raise SourceUnavailable(
                "budget",
                f"Tavily monthly budget reached ({snap['used']} of {snap['budget']} credits used in {snap['month']}). "
                "No call was made. Raise TAVILY_MONTHLY_BUDGET or wait for next month.",
            )
        body = {
            "query": query,
            "search_depth": self.search_depth,
            "max_results": self.max_results,
            "include_domains": domains,
            "exact_match": True,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": True,
        }
        try:
            resp = client.post(
                self.endpoint, json=body, headers={"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
            )
        except httpx.TimeoutException as exc:
            # a timed-out call may still have been billed: keep the reservation
            raise SourceUnavailable("timeout", "Tavily did not answer in time.") from exc
        except httpx.HTTPError as exc:
            self.ledger.refund(self.cost)  # the request never reached Tavily
            raise SourceUnavailable("unreachable", "Tavily could not be reached.") from exc

        status = resp.status_code
        if status != 200:
            if status < 500:
                self.ledger.refund(self.cost)  # 4xx (incl. 429/432/433) are not billed; a 5xx may have been, so keep it
            self._raise_for_status(resp)
        data = json_object(resp, "Tavily")
        usage = data.get("usage")
        if isinstance(usage, dict):
            reported = as_int(usage.get("credits"))
            if reported is not None:
                self.ledger.record_reported(reported)
        results = data.get("results")
        if not isinstance(results, list):
            return []
        cands: list[Candidate] = []
        for r in results:
            if not isinstance(r, dict):
                continue
            url, title, content = r.get("url"), r.get("title"), r.get("content")
            c = candidate_from_url(
                url if isinstance(url, str) else None,
                source=self.name,
                title=title if isinstance(title, str) else None,
                snippet=content if isinstance(content, str) else None,
                fetched_at=ctx.now(),
                extras={"score": as_float(r.get("score")), "query": query},
                allowed_platforms=allowed,
            )
            if c is not None:
                cands.append(c)
        return cands

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        status = resp.status_code
        retry_after = parse_retry_after(resp.headers.get("retry-after"))
        if status == 401:
            raise SourceUnavailable("invalid_key", "Tavily rejected the API key (401). Check TAVILY_API_KEY in server/.env.")
        if status in (432, 433):
            kind = "plan" if status == 432 else "pay-as-you-go"
            raise SourceUnavailable(
                "quota", f"Tavily {kind} usage limit reached ({status}). Upgrade the plan or wait for the limit to reset.", retry_after
            )
        if status == 429:
            raise SourceUnavailable("rate_limited", "Tavily is rate limiting requests (429).", retry_after)
        if status in (400, 422):
            raise SourceUnavailable("bad_request", f"Tavily rejected the request ({status}).")
        if status in (403, 404):
            raise SourceUnavailable("forbidden", f"Tavily refused the request ({status}).")
        if status >= 500:
            raise SourceUnavailable("unreachable", f"Tavily had a server problem ({status}).", retry_after)
        raise SourceUnavailable("unexpected_status", f"Tavily answered with HTTP {status}.")

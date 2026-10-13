"""Tiny glue: allowlist -> name match -> scorer. Returns None for official assets (never scored)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .allowlist import Allowlist
from .llm import LLMIntentResult
from .models import BrandProfileInput, CandidateApp, CandidateSocial, OfficialAssets
from .name_match import BRAND_NAME, FLAGGED, REVIEW, NameMatcher, NameMatchResult
from .scoring import ScoreResult, handle_from_url, score_app, score_social
from .urls import parse_store_url

_RANK = {FLAGGED: 3, BRAND_NAME: 2, REVIEW: 1}


@dataclass
class Detection:
    kind: str  # "social" | "app"
    score: ScoreResult
    name_match: NameMatchResult
    matched_text: str | None = None  # which field produced the best name match
    status: str = "pending_review"  # a human approves or rejects; the engine only scores
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "status": self.status,
            "matched_text": self.matched_text,
            "verdict": self.name_match.verdict,
            "name_match_pct": self.name_match.name_match_pct,
            "techniques": self.name_match.techniques,
            "squat_category": self.name_match.squat_category,
            **self.score.to_dict(),
        }


def best_name_match(matcher: NameMatcher, texts: list[str | None]) -> tuple[NameMatchResult, str | None]:
    best: tuple[NameMatchResult, str | None] | None = None
    for t in texts:
        if not t or not t.strip():
            continue
        r = matcher.match(t)
        key = (_RANK.get(r.verdict, 0), r.name_match_pct)
        if best is None or key > (_RANK.get(best[0].verdict, 0), best[0].name_match_pct):
            best = (r, t)
    return best or (NameMatchResult("safe", 0, notes=["No name to compare."]), None)


def make_matcher(brand: BrandProfileInput) -> NameMatcher:
    return NameMatcher(
        brand.name,
        brand.aliases,
        benign_words=set(brand.extra_benign_words),
        impersonation_words=set(brand.extra_impersonation_words),
    )


def evaluate_social_candidate(
    brand: BrandProfileInput,
    official: OfficialAssets,
    candidate: CandidateSocial,
    *,
    today: date | None = None,
    llm: LLMIntentResult | None = None,
    matcher: NameMatcher | None = None,
    allowlist: Allowlist | None = None,
) -> Detection | None:
    """Allowlist first (exact handle/URL only), then name match, then score. None when official."""
    if (allowlist or Allowlist(official)).check_social(candidate):
        return None
    matcher = matcher or make_matcher(brand)
    name, matched = best_name_match(matcher, [candidate.handle, candidate.display_name, handle_from_url(candidate.url)])
    score = score_social(candidate, official, name, today=today, llm=llm, official_description=brand.description)
    return Detection("social", score, name, matched)


def _package_name_text(app_id: str | None) -> str | None:
    if not app_id or "." not in app_id:
        return None
    parts = app_id.split(".")
    if parts[0].lower() in ("com", "in", "org", "net", "io", "co", "app"):
        parts = parts[1:]
    return ".".join(parts)


def evaluate_app_candidate(
    brand: BrandProfileInput,
    official: OfficialAssets,
    candidate: CandidateApp,
    *,
    today: date | None = None,
    matcher: NameMatcher | None = None,
    allowlist: Allowlist | None = None,
) -> Detection | None:
    if (allowlist or Allowlist(official)).check_app(candidate):
        return None
    matcher = matcher or make_matcher(brand)
    app_id = candidate.app_id
    if candidate.url:
        parsed = parse_store_url(candidate.url)
        if parsed.ok and parsed.handle and not app_id:
            app_id = parsed.handle
    name, matched = best_name_match(matcher, [candidate.title, _package_name_text(app_id)])
    score = score_app(candidate, brand, official, name, today=today, publisher_matcher=matcher)
    return Detection("app", score, name, matched)

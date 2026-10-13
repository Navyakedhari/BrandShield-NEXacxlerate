"""Explainable scorer.

Every point comes from a reason `{signal, points, max_points, note}`. The sum of reasons equals `raw_total`.
Each category is capped at its own maximum when the reasons are produced (so the reasons already show the
capped points); only then is the grand total capped at 100. `score = min(100, raw_total)` and
`capped` says whether that last cap changed anything. The maxima add up to more than 100 on purpose:
a candidate that looks bad on several fronts saturates at 100.

Social max: name 34, logo 28, added words 12, bio 28, account 18 (= 120).
App max:    name 24, icon 30, publisher 22 (20-22), description 17, store 10 (= 103).
Publisher-difference points count ONLY when another brand signal (name, icon or description) already
scored above zero: an unrelated app by another publisher is not suspicious on that basis alone.
Bands: 85+ high, 70-84 medium, below 70 low (same as app.core.pipeline.bands).
The engine only scores; a human approves or rejects every detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .llm import LLMIntentResult
from .logo_similarity import LIKELY_COPY_MIN, SIMILAR_MIN
from .models import BrandProfileInput, CandidateApp, CandidateSocial, OfficialAssets
from .name_match import BRAND_NAME, FLAGGED, REVIEW, NameMatcher, NameMatchResult
from .text_signals import analyze_bio, description_overlap, scam_points
from .urls import MIRROR_STORES, parse_profile_url
from .vocab import STRONG_SCAM_WORDS

HIGH_MIN = 85
MEDIUM_MIN = 70

SOCIAL_MAX = {"name": 34, "logo": 28, "added_words": 12, "bio": 28, "account": 18}
APP_MAX = {"name": 24, "icon": 30, "publisher": 22, "description": 17, "store": 10}
SHORTENER_HOSTS = ("bit.ly", "tinyurl.com", "t.co", "cutt.ly", "goo.gl", "is.gd", "rb.gy", "ow.ly", "shorturl.at", "tiny.cc")


def band_for(score: int) -> str:
    if score >= HIGH_MIN:
        return "high"
    if score >= MEDIUM_MIN:
        return "medium"
    return "low"


@dataclass(frozen=True)
class Reason:
    signal: str
    points: int
    max_points: int
    note: str

    def to_dict(self) -> dict:
        return {"signal": self.signal, "points": self.points, "max_points": self.max_points, "note": self.note}


@dataclass
class ScoreResult:
    score: int  # 0-100 after the overall cap
    raw_total: int  # sum of reason points, before the overall cap
    band: str
    reasons: list[Reason] = field(default_factory=list)

    @property
    def capped(self) -> bool:
        return self.raw_total > self.score

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "raw_total": self.raw_total,
            "capped": self.capped,
            "band": self.band,
            "reasons": [r.to_dict() for r in self.reasons],
        }


def _finish(reasons: list[Reason]) -> ScoreResult:
    raw = sum(r.points for r in reasons)
    score = min(100, raw)
    return ScoreResult(score, raw, band_for(score), reasons)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---- shared signal helpers -----------------------------------------------------------------------


def name_points(name: NameMatchResult, max_points: int) -> tuple[int, str]:
    if name.verdict == FLAGGED:
        return round(max_points * name.name_match_pct / 100), "; ".join(name.notes)
    if name.verdict == BRAND_NAME:
        return round(max_points * 0.9), "Uses the exact brand name but is not on the allowlist, so it may be the brand or an impersonator."
    if name.verdict == REVIEW:
        return round(max_points * name.name_match_pct / 100 * 0.5), "Partial / uncertain name match (half weight). " + "; ".join(name.notes)
    return 0, "; ".join(name.notes) or "Name does not resemble the brand."


def image_points(sim: float | None, max_points: int) -> int:
    """Below SIMILAR_MIN nothing; from SIMILAR_MIN (a third of the points) rising linearly to the maximum at 90."""
    if sim is None or sim < SIMILAR_MIN:
        return 0
    low = max_points / 3
    return round(low + (max_points - low) * _clamp((sim - SIMILAR_MIN) / (90 - SIMILAR_MIN)))


def _image_note(sim: float | None, what: str) -> str:
    if sim is None:
        return f"No {what} comparison available."
    if sim >= LIKELY_COPY_MIN:
        return f"The {what} looks like a copy of the official one (similarity {sim:.0f}/100)."
    if sim >= SIMILAR_MIN:
        return f"The {what} is similar to the official one (similarity {sim:.0f}/100); could be edited or cropped."
    return f"The {what} does not resemble the official one (similarity {sim:.0f}/100)."


def _to_date(d: date | datetime | None) -> date | None:
    if isinstance(d, datetime):
        return d.date()
    return d


# ---- social ------------------------------------------------------------------------------------


def score_social(
    candidate: CandidateSocial,
    official: OfficialAssets,
    name: NameMatchResult,
    *,
    today: date | None = None,
    llm: LLMIntentResult | None = None,
    official_description: str | None = None,
) -> ScoreResult:
    today = today or date.today()
    reasons: list[Reason] = []

    pts, note = name_points(name, SOCIAL_MAX["name"])
    if pts:
        reasons.append(Reason("name_similarity", pts, SOCIAL_MAX["name"], note))

    sim = candidate.logo_similarity
    pts = image_points(sim, SOCIAL_MAX["logo"])
    if pts:
        reasons.append(Reason("logo_similarity", pts, SOCIAL_MAX["logo"], _image_note(sim, "profile picture")))

    # added words only count when the name already matched the brand
    if name.verdict in (FLAGGED, REVIEW) and name.added_words:
        strong = [w for w in name.added_words if w in STRONG_SCAM_WORDS]
        if strong:
            pts, why = 12, f"Added scam-flavoured words: {', '.join(strong)}."
        elif name.impersonation_words:
            pts = min(12, 8 + 2 * (len(name.impersonation_words) - 1))
            why = f"Added words often used to pose as the company: {', '.join(name.impersonation_words)}."
        elif name.extra_word_kind == "digits":
            pts, why = 3, "Numbers added to the brand name."
        else:
            pts, why = 2, f"Other added words: {' '.join(name.added_words)}."
        reasons.append(Reason("added_words", pts, SOCIAL_MAX["added_words"], why))

    # bio: scam intent (+ optional LLM), capped
    sigs = analyze_bio(candidate.bio, candidate.bio_links)
    bio_pts = scam_points(sigs)
    bio_notes = [f"{s.id} (+{s.points}): {s.note}" for s in sigs]
    if llm is not None and llm.scam_likelihood >= 0.6:
        extra = round(10 * llm.scam_likelihood)
        bio_pts += extra
        bio_notes.append(f"llm_intent (+{extra}): the optional AI check judged the text likely scam-related. {llm.rationale}".strip())
    if official_description and candidate.bio:
        ov = description_overlap(candidate.bio, official_description, min_ngrams=2)
        if ov.score >= 30:
            extra = round(8 * _clamp(ov.score / 70))
            bio_pts += extra
            bio_notes.append(f"copied_text (+{extra}): {ov.note}")
    if bio_pts:
        capped = min(bio_pts, SOCIAL_MAX["bio"])
        suffix = f" (capped from {bio_pts})" if capped < bio_pts else ""
        reasons.append(Reason("bio_signals", capped, SOCIAL_MAX["bio"], " ".join(bio_notes) + suffix))

    reasons.extend(_account_reasons(candidate, official, today))
    return _finish(reasons)


def _account_reasons(c: CandidateSocial, official: OfficialAssets, today: date) -> list[Reason]:
    out: list[Reason] = []
    created = _to_date(c.created_at)
    if created is not None:
        age = (today - created).days
        if age < 14:
            out.append(Reason("account_age", 6, 6, f"Account is only {max(age, 0)} days old."))
        elif age < 60:
            out.append(Reason("account_age", 4, 6, f"Account is young ({age} days old)."))
        elif age < 180:
            out.append(Reason("account_age", 2, 6, f"Account is fairly new ({age} days old)."))
    if c.posts is not None and c.posts <= 5:
        out.append(Reason("few_posts", 3, 3, f"Only {c.posts} posts."))
    if c.followers is not None and c.following is not None:
        if c.following >= 500 and c.following >= 10 * max(c.followers, 1):
            out.append(Reason("follower_oddity", 4, 4, f"Follows {c.following} accounts but has only {c.followers} followers."))
        elif c.followers >= 10000 and c.posts is not None and c.posts < 10:
            out.append(Reason("follower_oddity", 4, 4, f"{c.followers} followers with only {c.posts} posts looks unnatural."))
    if c.verified is False:
        out.append(Reason("unverified", 2, 2, "Account is not verified."))
    if c.bio is not None and not c.bio.strip():
        out.append(Reason("empty_bio", 1, 1, "Profile has no bio."))
    domains = {d.lower().removeprefix("www.") for d in official.domains}
    foreign = []
    for link in c.bio_links:
        m = re.match(r"^(?:https?://)?(?:www\.)?([^/\s:?#]+)", link.strip(), re.I)
        host = m.group(1).lower() if m else ""
        if host and domains and not any(host == d or host.endswith("." + d) for d in domains) and host not in SHORTENER_HOSTS:
            foreign.append(host)
    if foreign:
        out.append(Reason("link_not_official", 2, 2, f"Bio links to {', '.join(foreign[:3])}, which is not an official domain."))
    return out


# ---- apps --------------------------------------------------------------------------------------

_SUFFIXES = {"inc", "ltd", "llc", "pvt", "private", "limited", "corp", "corporation", "co", "company", "plc", "gmbh"}


def normalize_publisher(name: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _SUFFIXES]
    return " ".join(words)


def score_app(
    candidate: CandidateApp,
    brand: BrandProfileInput,
    official: OfficialAssets,
    name: NameMatchResult,
    *,
    today: date | None = None,
    publisher_matcher: NameMatcher | None = None,
) -> ScoreResult:
    today = today or date.today()
    reasons: list[Reason] = []

    pts, note = name_points(name, APP_MAX["name"])
    if pts:
        reasons.append(Reason("name_similarity", pts, APP_MAX["name"], note))

    pts = image_points(candidate.icon_similarity, APP_MAX["icon"])
    if pts:
        reasons.append(Reason("icon_similarity", pts, APP_MAX["icon"], _image_note(candidate.icon_similarity, "app icon")))

    desc_pts = 0
    if brand.description and candidate.description:
        ov = description_overlap(candidate.description, brand.description)
        if ov.score >= 10:
            desc_pts = round(APP_MAX["description"] * _clamp(ov.score / 60))
            shared = f" Shared phrase: \"{ov.shared_phrases[0]}\"." if ov.shared_phrases else ""
            reasons.append(Reason("description_overlap", desc_pts, APP_MAX["description"], ov.note + shared))

    brand_signal = any(r.points > 0 for r in reasons)
    reasons.extend(_publisher_reason(candidate, official, brand_signal, publisher_matcher or NameMatcher(brand.name, brand.aliases)))
    reasons.extend(_store_reasons(candidate, today))
    return _finish(reasons)


def _publisher_reason(c: CandidateApp, official: OfficialAssets, brand_signal: bool, matcher: NameMatcher) -> list[Reason]:
    mx = APP_MAX["publisher"]
    pub = (c.publisher or "").strip()
    if not pub:
        return []
    known = {normalize_publisher(p) for p in official.publisher_names if p.strip()}
    if not known:
        return [Reason("publisher_differs", 0, mx, "No official publisher is on file, so the publisher cannot be compared.")]
    if normalize_publisher(pub) in known:
        return []
    if not brand_signal:
        return [Reason("publisher_differs", 0, mx, f'Publisher "{pub}" differs from the official one, but no name, icon or description signal matched, so it is not counted.')]
    claims = matcher.match(pub).verdict in (FLAGGED, BRAND_NAME, REVIEW)
    if claims:
        return [Reason("publisher_differs", 22, mx, f'Publisher "{pub}" is not an official publisher but its name resembles the brand.')]
    return [Reason("publisher_differs", 20, mx, f'Publisher "{pub}" is not an official publisher.')]


def _store_reasons(c: CandidateApp, today: date) -> list[Reason]:
    out: list[Reason] = []
    mirror = c.third_party_mirror if c.third_party_mirror is not None else c.store.lower() in MIRROR_STORES
    if mirror:
        out.append(Reason("third_party_mirror", 4, 4, "Listed on a third-party APK site, not an official store."))
    if c.installs is not None and c.installs < 1000:
        out.append(Reason("few_installs", 3, 3, f"Only {c.installs} installs."))
    pub = _to_date(c.published_at)
    if pub is not None and 0 <= (today - pub).days < 30:
        out.append(Reason("new_listing", 2, 2, f"Listing is only {(today - pub).days} days old."))
    if c.rating_count is not None and c.rating_count < 10:
        out.append(Reason("few_ratings", 1, 1, f"Only {c.rating_count} ratings."))
    return out


def handle_from_url(url: str | None) -> str | None:
    if not url:
        return None
    p = parse_profile_url(url)
    return p.handle if p.ok else None

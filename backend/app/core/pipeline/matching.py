"""Glue between the stored brand profile / Settings page and the pure detection engine.

* `build_matcher`: NameMatcher with the brand name, aliases and the configured impersonation words.
* `apply_rules`: the Settings "look-alike rules" switches (a technique that is switched off cannot flag on its own).
* `BrandAllowlist`: exact handle / URL / package id only, checked BEFORE scoring. Never excludes by name.
* Labels and short summaries used by the detection and look-alike screens.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.collectors.base import Candidate
from app.core.engine.allowlist import Allowlist
from app.core.engine.domain_match import normalize_url, official_registrables
from app.core.engine.models import BrandProfileInput, OfficialAssets
from app.core.engine.name_match import (
    ADDED_WORD,
    CHARACTER_SWAP,
    DIGIT_OR_SYMBOL_SWAP,
    FLAGGED,
    HOMOGLYPH,
    MISSING_OR_EXTRA_LETTER,
    REVIEW,
    SPACING_OR_SEPARATOR,
    TECHNIQUE_LABELS,
    TRANSPOSED,
    NameMatcher,
    NameMatchResult,
)
from app.core.engine.pipeline import make_matcher
from app.models import Brand

DEFAULT_IMPERSONATION_WORDS = ["support", "helpline", "official", "refund", "rewards", "kyc"]
DEFAULT_RULES = {
    "character_swaps": True,
    "digits_and_symbols": True,
    "added_words": True,
    "spacing_and_separators": True,
    "transposed_or_missing": True,
}
TECHNIQUE_RULE = {
    CHARACTER_SWAP: "character_swaps",
    HOMOGLYPH: "character_swaps",
    DIGIT_OR_SYMBOL_SWAP: "digits_and_symbols",
    ADDED_WORD: "added_words",
    SPACING_OR_SEPARATOR: "spacing_and_separators",
    TRANSPOSED: "transposed_or_missing",
    MISSING_OR_EXTRA_LETTER: "transposed_or_missing",
}
RULE_LABELS = {
    "character_swaps": "Character swaps (rn for m, look-alike letters)",
    "digits_and_symbols": "Digits and symbols for letters",
    "added_words": "Added words",
    "spacing_and_separators": "Spacing and separators",
    "transposed_or_missing": "Transposed or missing letters",
}
SIGNAL_LABELS = {
    "name_similarity": "Name similarity",
    "logo_similarity": "Logo match",
    "icon_similarity": "Icon match",
    "added_words": "Added word",
    "bio_signals": "Bio content",
    "description_overlap": "Description overlap",
    "publisher_differs": "Publisher",
    "account_age": "Account signals",
    "few_posts": "Account signals",
    "follower_oddity": "Account signals",
    "unverified": "Account signals",
    "empty_bio": "Account signals",
    "link_not_official": "Account signals",
    "third_party_mirror": "Store signals",
    "few_installs": "Store signals",
    "new_listing": "Store signals",
    "few_ratings": "Store signals",
    "domain_similarity": "Domain similarity",
    "rapidfuzz_similarity": "String similarity (RapidFuzz)",
    "deceptive_subdomain": "Deceptive subdomain",
    "idn_homograph": "Look-alike characters (IDN)",
    "url_reputation": "URL reputation (Google Safe Browsing)",
}
# a reason that ties the candidate to THIS brand (account/store oddities alone never make a detection)
BRAND_SIGNALS = frozenset({"name_similarity", "logo_similarity", "icon_similarity", "description_overlap",
                           "domain_similarity", "rapidfuzz_similarity", "deceptive_subdomain", "idn_homograph"})


def build_matcher(brand: Brand | str, aliases: list[str] | None = None, words: list[str] | None = None) -> NameMatcher:
    name = brand if isinstance(brand, str) else brand.name
    al = (aliases if aliases is not None else list(brand.aliases or [])) if not isinstance(brand, str) else (aliases or [])
    prof = BrandProfileInput(name=name, aliases=al, extra_impersonation_words=[w for w in (words or []) if w])
    return make_matcher(prof)


def apply_rules(result: NameMatchResult, rules: dict[str, bool]) -> NameMatchResult:
    """A flagged name whose techniques are ALL switched off is downgraded to `review` (half weight, human look)."""
    if result.verdict != FLAGGED or not result.techniques:
        return result
    enabled = [t for t in result.techniques if rules.get(TECHNIQUE_RULE.get(t, ""), True)]
    if enabled:
        return result
    result.verdict = REVIEW
    result.notes = [*result.notes, "Every technique used is switched off in Settings, so this is not flagged on its own."]
    return result


def technique_labels(result: NameMatchResult) -> list[str]:
    return [TECHNIQUE_LABELS.get(t, t) for t in result.techniques]


def official_assets(brand: Brand) -> OfficialAssets:
    handles: dict[str, list[str]] = {}
    urls: list[str] = []
    for a in brand.accounts:
        handles.setdefault(a.platform, []).append(a.handle)
        urls.append(f"https://{a.url}" if "://" not in a.url else a.url)
    play = [a.package_id for a in brand.apps if "google_play" in (a.stores or [])]
    apple = [a.package_id for a in brand.apps if "app_store" in (a.stores or []) and a.package_id.isdigit()]
    apple += [a.store_id for a in brand.apps if a.store_id and a.store_id not in apple]  # numeric App Store id stored with the bundle id
    return OfficialAssets(
        social_handles=handles,
        social_urls=urls,
        play_package_ids=play,
        apple_app_ids=apple,
        domains=list(brand.domains or []),
        publisher_names=sorted({a.publisher for a in brand.apps if a.publisher}),
    )


@dataclass(frozen=True)
class OfficialHit:
    reason: str  # exact_handle | exact_url | package_id | apple_id | bundle_id
    rule: str  # human readable
    value: str
    kind: str  # account | app
    account_id: int | None = None
    app_id: int | None = None


class BrandAllowlist:
    """Exact-asset allowlist for one brand. Name alone never matches."""

    def __init__(self, brand: Brand) -> None:
        self.assets = official_assets(brand)
        self.engine = Allowlist(self.assets)
        self._account_ids = {(a.platform, a.handle.lower()): a.id for a in brand.accounts}
        self._play_ids = {a.package_id.lower(): a.id for a in brand.apps if "google_play" in (a.stores or [])}
        self._apple_bundles = {a.package_id.lower(): a.id for a in brand.apps if "app_store" in (a.stores or [])}
        self._apple_numeric = {a.store_id: a.id for a in brand.apps if a.store_id}
        self._apple_numeric.update({a.package_id: a.id for a in brand.apps if "app_store" in (a.stores or []) and a.package_id.isdigit()})
        self._domains = official_registrables(list(brand.domains or []))
        verified = {x.asset_key for x in brand.verifications if x.asset_type == "publisher" and x.status == "verified"}
        self._publishers = {(p.store, p.publisher_key) for p in brand.publishers if p.publisher_key in verified and p.publisher_id}

    def learn_apple_ids(self, candidates: list[Candidate]) -> int:
        """An App Store listing found with an official BUNDLE id (iTunes) also tells us its numeric id. The same app arriving from a
        source that only exposes the numeric id (a Tavily URL) is then recognised as official too. Per scan, in memory only: the
        registry itself is not changed. Returns how many ids were learned."""
        n = 0
        for c in candidates:
            if c.kind != "app" or c.platform != "app_store" or not c.handle or c.handle.lower() not in self._apple_bundles:
                continue
            numeric = str(c.extras.get("apple_app_id") or "").strip()
            if numeric.isdigit() and numeric not in self.engine.apple_ids:
                self.engine.apple_ids.add(numeric)
                self._apple_numeric[numeric] = self._apple_bundles[c.handle.lower()]
                n += 1
        return n
    def check(self, c: Candidate) -> OfficialHit | None:
        if c.kind == "web":
            return self._check_web(c)
        eng = c.to_engine()
        if c.kind == "social":
            m = self.engine.check_social(eng)  # type: ignore[arg-type]
            if m is None:
                return None
            platform = "x" if c.platform == "twitter" else c.platform
            handle = (c.handle or m.value).lstrip("@").lower()
            acc = self._account_ids.get((platform, handle)) or self._account_ids.get((platform, m.value.lower()))
            return OfficialHit("exact_handle" if m.asset == "handle" else "exact_url", m.reason, m.value, "account", account_id=acc)
        m = self.engine.check_app(eng)  # type: ignore[arg-type]
        if m is not None:
            app_id = self._play_ids.get(m.value.lower()) if m.asset == "package_id" else self._apple_numeric.get(m.value)
            reason = {"package_id": "package_id", "apple_id": "apple_id"}.get(m.asset, "package_id")
            return OfficialHit(reason, m.reason, m.value, "app", app_id=app_id)
        pid = str(c.extras.get("publisher_id") or "").strip().lower()
        if pid and ((c.platform, pid) in self._publishers or ("any", pid) in self._publishers):
            return OfficialHit("publisher_id", f"Publisher id {pid} is a VERIFIED official publisher of the brand.", pid, "app")
        # App Store listings expose the bundle id; an exact bundle id of an official app is official.
        if c.platform == "app_store" and c.handle and c.handle.lower() in self._apple_bundles:
            return OfficialHit("bundle_id", "Exact official App Store bundle id.", c.handle, "app", app_id=self._apple_bundles[c.handle.lower()])
        return None


    def _check_web(self, c: Candidate) -> OfficialHit | None:
        n = normalize_url(c.profile_url or c.handle)
        if n.ok and n.registrable_domain and n.registrable_domain in self._domains:
            return OfficialHit("official_domain", f"Registrable domain {n.registrable_domain} is an official domain of the brand.",
                               n.registrable_domain, "domain")
        return None


def reason_dicts(reasons) -> list[dict]:
    return [
        {
            "signal": r.signal,
            "label": SIGNAL_LABELS.get(r.signal, r.signal.replace("_", " ").capitalize()),
            "detail": r.note,
            "points": r.points,
            "max_points": r.max_points,
        }
        for r in reasons
    ]


def summary_and_tags(name: NameMatchResult, reasons: list[dict], logo_pct: float | None) -> tuple[str, list[str]]:
    tags: list[str] = []
    parts: list[str] = []
    for t in technique_labels(name):
        parts.append(t)
        tags.append(t.capitalize())
    if name.added_words:
        w = ", ".join(name.added_words[:3])
        parts.append(f"added word {w}" if len(name.added_words) == 1 else f"added words {w}")
        tags.append(f"Added word: {w}")
    if logo_pct is not None and logo_pct >= 55:
        tags.append(f"Logo {logo_pct:.0f}% match")
        parts.append(f"logo {logo_pct:.0f}% match")
    for r in reasons:
        if r["signal"] == "bio_signals" and r["points"] > 0:
            tags.append("Suspicious bio")
            parts.append("suspicious bio")
        if r["signal"] == "publisher_differs" and r["points"] > 0:
            tags.append("Different publisher")
            parts.append("different publisher")
        if r["signal"] == "third_party_mirror" and r["points"] > 0:
            tags.append("Third-party APK site")
    if not parts and name.verdict != "safe":
        parts.append(f"name resembles the brand ({name.name_match_pct}%)")
    seen: set[str] = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))]
    return (", ".join(parts)[:300] or "No strong signal"), tags


def is_hit(score: int, threshold: int, reasons: list[dict], name_verdict: str | None, *, lookalike: bool = False) -> bool:
    """A candidate becomes a detection when it has evidence tied to THIS brand (name, logo/icon or description) AND
    either its score reaches the sensitivity threshold or the engine flagged its name outright. A flagged name always
    goes to review; the sensitivity setting moves the bar for weaker evidence only. `lookalike` is the domain analysis verdict of a web
    candidate (deceptive subdomain, look-alike characters ...)."""
    brand_signal = any(r["signal"] in BRAND_SIGNALS and r["points"] > 0 for r in reasons)
    return brand_signal and (score >= threshold or name_verdict == FLAGGED or lookalike)

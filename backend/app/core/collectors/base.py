"""Collector contract: normalized Candidate objects, the SourceUnavailable error, URL and title helpers.

A collector fetches raw material from ONE provider and returns `Candidate`s. It must never raise anything
except `SourceUnavailable`; the orchestrator (orchestrator.py) catches that, records the source status and
carries on with the other collectors. Collectors do not touch the engine's scoring.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import httpx

from app.config.time import utcnow
from app.core.engine.domain_match import normalize_url
from app.core.engine.models import CandidateApp, CandidateSocial
from app.core.engine.urls import ParsedUrl, parse_url

SOCIAL_PLATFORMS = ("instagram", "facebook", "x", "telegram", "linkedin", "youtube")
STORE_PLATFORMS = ("google_play", "app_store", "third_party_apk")
WEB_PLATFORM = "web"  # look-alike domains / websites: a third candidate category beside social and apps (no Source Health row)
# Platforms shown on the Source Health page (youtube only appears for official accounts).
SOURCE_PLATFORMS = ("instagram", "facebook", "x", "telegram", "linkedin", "google_play", "app_store", "third_party_apk")

# Reasons that will not fix themselves: do not retry automatically (a human changes config or retries).
PERMANENT_REASONS = frozenset({"no_key", "invalid_key", "not_installed", "not_configured", "unsafe_base_url", "json_disabled", "bad_request"})


class SourceUnavailable(Exception):  # noqa: N818 - name is part of the documented contract
    """The data source cannot serve this request right now. Never blocks a scan."""

    def __init__(self, reason: str, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason  # short machine code: no_key, invalid_key, quota, rate_limited, unreachable, timeout, ...
        self.message = message or reason
        self.retry_after = retry_after  # seconds, when the provider told us


@dataclass
class Candidate:
    """One account/page/app found by a collector, normalized across providers."""

    platform: str  # instagram|facebook|x|telegram|linkedin|youtube | google_play|app_store|third_party_apk
    kind: str  # "social" | "app" | "web"  (web: handle = ASCII host, profile_url = canonical URL; never fetched)
    handle: str | None  # profile handle/id, or package / bundle id for apps
    display_name: str | None
    profile_url: str | None
    source: str  # collector name: tavily|searxng|itunes|google_play|sample
    fetched_at: datetime = field(default_factory=utcnow)
    bio: str | None = None  # bio (social) or description (apps)
    title: str | None = None  # raw result title
    snippet: str | None = None  # raw result text snippet
    store: str | None = None  # engine store name for apps: google_play|apple_app_store|apkpure|...
    publisher: str | None = None
    icon_url: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    followers: int | None = None
    following: int | None = None
    posts: int | None = None
    created_at: date | None = None
    verified: bool | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    stale: bool = False  # True when served from the cache because the source was unavailable
    cached_at: datetime | None = None
    # SAMPLE-ONLY: generated image bytes (see sample_images.py). The scan pipeline ignores this for any
    # candidate whose source is not "sample", and it is never serialized (cache / evidence snapshots).
    inline_image: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def data_source(self) -> str:
        """Value for the `source` column of detections: only the sample collector produces 'sample'."""
        return "sample" if self.source == "sample" else "live"

    @property
    def key(self) -> tuple[str, str]:
        return (self.platform, (self.handle or self.profile_url or "").lower())

    # ---- (de)serialization for the result cache ----
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("inline_image", None)
        d["fetched_at"] = self.fetched_at.isoformat()
        d["cached_at"] = self.cached_at.isoformat() if self.cached_at else None
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Candidate:
        d = dict(d)
        d["fetched_at"] = datetime.fromisoformat(d["fetched_at"]) if d.get("fetched_at") else utcnow()
        d["cached_at"] = datetime.fromisoformat(d["cached_at"]) if d.get("cached_at") else None
        d["created_at"] = date.fromisoformat(d["created_at"]) if d.get("created_at") else None
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    # ---- hand-off to the detection engine ----
    def to_engine(self) -> CandidateSocial | CandidateApp:
        if self.kind == "web":
            raise ValueError("web candidates are analysed by engine.domain_match, not by the social/app scorers")
        if self.kind == "social":
            return CandidateSocial(
                platform=self.platform,
                handle=self.handle,
                display_name=self.display_name,
                url=self.profile_url,
                bio=self.bio,
                followers=self.followers,
                following=self.following,
                posts=self.posts,
                created_at=self.created_at,
                verified=self.verified,
            )
        store = self.store or {"google_play": "google_play", "app_store": "apple_app_store"}.get(self.platform, self.platform)
        app_id = self.handle
        if store == "apple_app_store" and self.extras.get("apple_app_id"):
            app_id = str(self.extras["apple_app_id"])  # the allowlist compares numeric App Store ids
        return CandidateApp(
            store=store,
            app_id=app_id,
            url=self.profile_url,
            title=self.display_name,
            publisher=self.publisher,
            description=self.bio,
            rating_count=self.rating_count,
            installs=self.extras["installs_count"] if isinstance(self.extras.get("installs_count"), int) else None,
            published_at=_iso_datetime(self.extras.get("released_at")),
        )


def _iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class BrandQuery:
    """Everything a collector may know about the brand (read-only snapshot, no DB objects)."""

    brand_id: int
    name: str
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    official_handles: dict[str, tuple[str, ...]] = field(default_factory=dict)  # platform -> bare handles
    official_apps: tuple[tuple[str, str, str], ...] = ()  # (name, package_id, publisher)
    publisher_names: tuple[str, ...] = ()
    # Raw bytes of the uploaded brand logo. Loaded only in sample mode, for the sample collector's generated images.
    logo_bytes: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def terms(self) -> list[str]:
        out: list[str] = []
        for t in (self.name, *self.aliases, *self.keywords):
            t = _clean_term(t)
            if t and t.lower() not in {o.lower() for o in out}:
                out.append(t)
        return out


@dataclass
class CollectContext:
    """Per-run knobs passed to every collector."""

    platforms: frozenset[str] = frozenset(SOURCE_PLATFORMS)  # only monitored platforms
    max_queries: int | None = None  # overrides the collector's own per-scan cap when lower
    now: Callable[[], datetime] = utcnow


class Collector(ABC):
    """Base class. Subclasses set `name`, `platforms`, and implement `collect`."""

    name: str = ""
    kind: str = "mixed"  # social | app | mixed
    platforms: tuple[str, ...] = ()  # source platforms this collector can report on
    # Notes from the last collect() call (partial results, skipped queries). Reset on every call.
    warnings: list[str]

    def __init__(self) -> None:
        self.warnings = []

    def is_configured(self) -> bool:
        return True

    @abstractmethod
    def collect(self, brand: BrandQuery, ctx: CollectContext) -> list[Candidate]:
        """Return candidates; raise SourceUnavailable (and only that) when the source cannot serve."""


# ---------------------------------------------------------------------------------------------------------
# HTTP helpers shared by collectors


def default_client_factory(timeout: float = 15.0) -> Callable[[], httpx.Client]:
    def make() -> httpx.Client:
        return httpx.Client(timeout=timeout, follow_redirects=False)

    return make


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After header: delta-seconds or an HTTP date. Returns seconds (>= 0) or None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(min(int(value), 7 * 86400))
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(value)
        base = now or utcnow()
        if when.tzinfo is None:
            from datetime import UTC

            when = when.replace(tzinfo=UTC)
        return max(0.0, min((when - base).total_seconds(), 7 * 86400))
    except (TypeError, ValueError):
        return None


def json_object(resp: httpx.Response, provider: str) -> dict[str, Any]:
    """Body as a JSON object, else SourceUnavailable('bad_response')."""
    try:
        data = resp.json()
    except ValueError as exc:
        raise SourceUnavailable("bad_response", f"{provider} returned a response that is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise SourceUnavailable("bad_response", f"{provider} returned an unexpected response shape.")
    return data


def as_text(value: Any, limit: int = 2000) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s[:limit] if s else None


def as_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_term(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").replace('"', " ")).strip()


# ---------------------------------------------------------------------------------------------------------
# Title cleaning

_SOCIAL_NAMES = r"(?:Instagram|Facebook|X|Twitter|Telegram|LinkedIn|YouTube)"
_TITLE_PATTERNS = [
    # "Acme (@acme) • Instagram photos and videos"  /  "... • Instagram"
    re.compile(r"\s*\(@[^)]*\)\s*[•·|\-–—/]?\s*" + _SOCIAL_NAMES + r"(?:\s+(?:photos and videos|reels|profile))*\s*$", re.I),
    re.compile(r"\s*[•·|]\s*" + _SOCIAL_NAMES + r"(?:\s+(?:photos and videos|reels|profile))*\s*$", re.I),
    re.compile(r"\s+[-–—/]\s*" + _SOCIAL_NAMES + r"\s*$", re.I),
    re.compile(r"\s*\(@[^)]*\)\s*$", re.I),
    re.compile(r"\s*:\s*(?:Overview|About|Home)\s*$", re.I),  # LinkedIn "Acme: Overview"
    re.compile(r"\s+[-–—|]\s*(?:Home|About|Posts|Videos|Photos|Reviews|Overview|Official(?: Page)?)\s*$", re.I),
    # stores
    re.compile(r"\s+[-–—]\s*Apps on Google Play\s*$", re.I),
    re.compile(r"\s+[-–—|]\s*Google Play(?: Store)?\s*$", re.I),
    re.compile(r"\s+on the App Store\s*$", re.I),
    re.compile(r"\s+[-–—|]\s*App Store\s*$", re.I),
    re.compile(r"\s+[-–—|]\s*(?:APKPure(?:\.com)?|APKMirror|Uptodown|Aptoide|APKCombo|APKMonk)[^|]*$", re.I),
    re.compile(r"\s+(?:APK|MOD APK)\s+(?:\d[\w.\-]*\s+)?(?:Download|for Android).*$", re.I),
    re.compile(r"\s+Download\s*(?:APK)?\s*$", re.I),
    re.compile(r"\s*(?:\.{3}|…)\s*$"),
]
_LEADING_ON = re.compile(r"^(?P<name>.+?)\s+on\s+" + _SOCIAL_NAMES + r"\s*:.*$", re.I)  # 'Acme on X: "tweet text"'


def clean_title(title: str | None) -> str | None:
    """Strip site suffixes like ' | Instagram', '(@handle) • Instagram photos and videos', '- Google Play'."""
    t = as_text(title, 300)
    if not t:
        return None
    m = _LEADING_ON.match(t)
    if m:
        t = m.group("name")
    for _ in range(4):  # patterns can stack: "Acme - Home | Facebook"
        before = t
        for pat in _TITLE_PATTERNS:
            t = pat.sub("", t).strip()
        if t == before:
            break
    t = t.strip(" -–—|•·/")
    return t or None


# ---------------------------------------------------------------------------------------------------------
# URL -> Candidate

_STORE_TO_PLATFORM = {"google_play": "google_play", "apple_app_store": "app_store"}


def candidate_from_url(
    url: str | None,
    *,
    source: str,
    title: str | None = None,
    snippet: str | None = None,
    fetched_at: datetime | None = None,
    extras: dict[str, Any] | None = None,
    allowed_platforms: frozenset[str] | None = None,
) -> Candidate | None:
    """Turn a search-result URL into a Candidate, or None when it is not a profile/listing page."""
    if not url:
        return None
    parsed: ParsedUrl = parse_url(url)
    if not parsed.ok or not parsed.handle:
        return None
    cleaned = clean_title(title)
    snippet_text = as_text(snippet)
    now = fetched_at or utcnow()
    if parsed.kind == "profile":
        platform = parsed.platform or ""
        if allowed_platforms is not None and platform not in allowed_platforms and platform != "youtube":
            return None
        return Candidate(
            platform=platform,
            kind="social",
            handle=parsed.handle,
            display_name=cleaned,
            profile_url=parsed.canonical_url,
            source=source,
            fetched_at=now,
            bio=snippet_text,
            title=as_text(title, 300),
            snippet=snippet_text,
            extras=dict(extras or {}),
        )
    store = parsed.store or ""
    platform = _STORE_TO_PLATFORM.get(store, "third_party_apk")
    if allowed_platforms is not None and platform not in allowed_platforms:
        return None
    ex = dict(extras or {})
    if platform == "third_party_apk":
        ex["mirror"] = store
    if store == "apple_app_store":
        ex["apple_app_id"] = parsed.handle
    return Candidate(
        platform=platform,
        kind="app",
        handle=parsed.handle,
        display_name=cleaned,
        profile_url=parsed.canonical_url,
        source=source,
        fetched_at=now,
        bio=snippet_text,
        title=as_text(title, 300),
        snippet=snippet_text,
        store=store,
        extras=ex,
    )


def candidate_from_web_url(
    url: str | None,
    *,
    source: str,
    title: str | None = None,
    snippet: str | None = None,
    fetched_at: datetime | None = None,
    extras: dict[str, Any] | None = None,
) -> Candidate | None:
    """A website candidate from a URL or bare host. None for unusable input and for raw IP hosts (no domain to compare).

    The URL is only normalized (lower-case, userinfo and fragment removed, IDN as punycode); it is never opened."""
    n = normalize_url(url)
    if not n.ok or n.is_ip or not n.host or n.registrable_domain is None:
        return None
    return Candidate(
        platform=WEB_PLATFORM, kind="web", handle=n.host, display_name=n.host_unicode, profile_url=n.canonical_url, source=source,
        fetched_at=fetched_at or utcnow(), bio=as_text(snippet), title=as_text(title, 300), snippet=as_text(snippet),
        extras={**(extras or {}), "registrable_domain": n.registrable_domain},
    )


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """Keep the first candidate per (platform, handle); later ones fill in missing fields."""
    seen: dict[tuple[str, str], Candidate] = {}
    for c in candidates:
        k = c.key
        if not k[1]:
            continue
        if k not in seen:
            seen[k] = c
            continue
        keep = seen[k]
        for f in ("display_name", "bio", "snippet", "publisher", "icon_url", "rating", "rating_count"):
            if getattr(keep, f) is None and getattr(c, f) is not None:
                setattr(keep, f, getattr(c, f))
    return list(seen.values())

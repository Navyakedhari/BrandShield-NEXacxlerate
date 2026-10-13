"""Allowlist: exact official handles / URLs / package ids are dropped BEFORE scoring.

Never excludes by name alone: a candidate that merely *uses* the brand's name is not official.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import CandidateApp, CandidateSocial, OfficialAssets
from .urls import parse_profile_url, parse_store_url


@dataclass(frozen=True)
class AllowlistMatch:
    asset: str  # "handle" | "url" | "package_id" | "apple_id" | "store_url"
    value: str
    reason: str


def _bare(handle: str) -> str:
    return handle.strip().lstrip("@").lower()


class Allowlist:
    def __init__(self, official: OfficialAssets) -> None:
        self.handles: dict[str, set[str]] = {}
        for platform, hs in official.social_handles.items():
            self.handles.setdefault(platform.lower(), set()).update(_bare(h) for h in hs if h.strip())
        self.profile_keys: set[tuple[str, str]] = set()
        for u in official.social_urls:
            p = parse_profile_url(u)
            if p.ok and p.platform and p.handle:
                self.profile_keys.add((p.platform, p.handle.lower()))
                self.handles.setdefault(p.platform, set()).add(p.handle.lower())
        self.play_ids = {p.strip().lower() for p in official.play_package_ids if p.strip()}
        self.apple_ids = {a.strip() for a in official.apple_app_ids if a.strip()}
        for u in official.store_urls:
            s = parse_store_url(u)
            if s.ok and s.handle:
                if s.store == "google_play":
                    self.play_ids.add(s.handle.lower())
                elif s.store == "apple_app_store":
                    self.apple_ids.add(s.handle)

    def check_social(self, c: CandidateSocial) -> AllowlistMatch | None:
        platform = c.platform.lower()
        if platform == "twitter":
            platform = "x"
        if c.handle and _bare(c.handle) in self.handles.get(platform, set()):
            return AllowlistMatch("handle", _bare(c.handle), f"Exact official {platform} handle.")
        if c.url:
            p = parse_profile_url(c.url)
            if p.ok and p.platform == platform and p.handle and p.handle.lower() in self.handles.get(platform, set()):
                return AllowlistMatch("url", p.canonical_url or c.url, f"Exact official {platform} profile URL.")
        return None

    def check_app(self, c: CandidateApp) -> AllowlistMatch | None:
        store, app_id = c.store.lower(), (c.app_id or "").strip()
        if c.url:
            p = parse_store_url(c.url)
            if p.ok and p.handle:
                store, app_id = p.store or store, p.handle
        if store == "google_play" and app_id.lower() in self.play_ids:
            return AllowlistMatch("package_id", app_id, "Exact official Google Play package id.")
        if store == "apple_app_store" and app_id in self.apple_ids:
            return AllowlistMatch("apple_id", app_id, "Exact official App Store id.")
        return None

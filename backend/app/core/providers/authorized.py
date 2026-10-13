"""Interface STUB for future authorized store / social data providers (an official partner API, a licensed data feed, a customer's own export).

Nothing here talks to any platform and no access is claimed: `NotConfiguredProvider` is the only implementation and always reports
that no authorized access exists. A real implementation must (1) use credentials the operator was granted by the platform, (2) return
`Candidate` objects with `source` set to its own name, and (3) never scrape. It would be wired in as a collector (see
`app/core/collectors/base.py`) so the allowlist-first pipeline, scoring and de-duplication apply unchanged."""

from __future__ import annotations

from typing import Protocol

from app.core.collectors.base import BrandQuery, Candidate


class AuthorizedProvider(Protocol):
    name: str
    platforms: tuple[str, ...]

    def is_authorized(self) -> bool:
        """True only when the operator holds granted credentials for this platform."""
        ...

    def search(self, brand: BrandQuery, *, limit: int = 50) -> list[Candidate]: ...


class NotConfiguredProvider:
    name = "none"
    platforms: tuple[str, ...] = ()

    def is_authorized(self) -> bool:
        return False

    def search(self, brand: BrandQuery, *, limit: int = 50) -> list[Candidate]:
        return []

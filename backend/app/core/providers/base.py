"""Shared envelope for provider results: honest verdicts and provenance labels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.config.time import iso_z

# Where a signal comes from. Shown next to every signal so nobody mistakes a local heuristic for a vendor verdict.
REAL_API = "real_api"  # a live answer from an external service
LOCAL_ALGORITHM = "local_algorithm"  # deterministic code in this repository
PRETRAINED_MODEL = "pretrained_model"  # a downloaded/hosted model
SYNTHETIC = "synthetic"  # sample / generated data: not real evidence
PROVENANCE = (REAL_API, LOCAL_ALGORITHM, PRETRAINED_MODEL, SYNTHETIC)

# Reputation verdicts. `clean` only means "not in this provider's lists", never "safe".
MATCH, CLEAN, UNKNOWN, FAILED = "match", "clean", "unknown", "failed"
VERDICTS = (MATCH, CLEAN, UNKNOWN, FAILED)


@dataclass(frozen=True)
class ReputationResult:
    url: str
    verdict: str  # match | clean | unknown | failed
    provider: str  # e.g. "google_safe_browsing"
    api_version: str | None = None
    checked_at: datetime | None = None  # None when no request was made (no key, budget used up)
    threat_types: tuple[str, ...] = ()
    reason: str | None = None  # machine code: no_key | quota | budget | timeout | invalid_key | server_error | bad_response | cached ...
    message: str = ""
    cached: bool = False

    @property
    def signal_status(self) -> str:
        """Risk-engine status: a match is `present`, a clean answer is `clean`, anything else is `unavailable` (never clean)."""
        return {MATCH: "present", CLEAN: "clean"}.get(self.verdict, "unavailable")

    def to_dict(self) -> dict:
        return {
            "url": self.url, "verdict": self.verdict, "provider": self.provider, "api_version": self.api_version,
            "checked_at": iso_z(self.checked_at) if self.checked_at else None, "threat_types": list(self.threat_types),
            "reason": self.reason, "message": self.message, "cached": self.cached, "provenance": REAL_API if self.checked_at else None,
        }

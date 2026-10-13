"""Request / response types for text analysis providers.

Two provenance labels are kept apart on purpose and shown next to every finding:

* `api_generated_assessment`: a hosted model wrote this judgement (Gemini, Hugging Face). It is an opinion, not evidence.
* `deterministic_similarity`: the local baseline (RapidFuzz / token overlap / phrase rules). Same input, same output.

Whatever a model returns is UNTRUSTED output: `Assessment` clamps and filters every field, and nothing in it can lower a score,
remove an allowlist match or confirm a detection.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

API_ASSESSMENT = "api_generated_assessment"
DETERMINISTIC = "deterministic_similarity"

CATEGORIES = ("fake_support", "kyc_lure", "fee_request", "giveaway", "investment", "impersonation", "phishing_link", "payment_request", "other")
KINDS = ("bio", "app_description")


class ProviderError(Exception):
    """A provider could not answer. `reason` is a short machine code (no_key, quota, invalid_key, timeout, bad_response, ...)."""

    def __init__(self, reason: str, message: str = "", *, retryable: bool = False, retry_after: float | None = None) -> None:
        super().__init__(message or reason)
        self.reason, self.message, self.retryable, self.retry_after = reason, message or reason, retryable, retry_after


class GeminiResponseSchema(BaseModel):
    """The JSON shape the model is asked to produce (kept constraint-free so every provider's schema dialect accepts it)."""

    scam_likelihood: float
    categories: list[str]
    impersonation_claims: list[str]
    rationale: str


class Assessment(BaseModel):
    """Validated, clamped model output. Unknown fields are ignored; out-of-range values are clamped; bad categories are dropped."""

    model_config = ConfigDict(extra="ignore")

    scam_likelihood: float = Field(ge=0.0, le=1.0)
    categories: list[str] = Field(default_factory=list)
    impersonation_claims: list[str] = Field(default_factory=list)
    rationale: str = ""

    @field_validator("scam_likelihood", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> float:
        if isinstance(v, bool) or not isinstance(v, int | float | str):
            raise ValueError("scam_likelihood must be a number")
        f = float(v)
        if f != f:  # NaN
            raise ValueError("scam_likelihood must be a number")
        return max(0.0, min(1.0, f))

    @field_validator("categories", mode="before")
    @classmethod
    def _cats(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for c in v:
            c = str(c).strip().lower().replace(" ", "_")
            if c in CATEGORIES and c not in out:
                out.append(c)
        return out[:6]

    @field_validator("impersonation_claims", mode="before")
    @classmethod
    def _claims(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [_plain(str(c), 120) for c in v if isinstance(c, str) and c.strip()][:5]

    @field_validator("rationale", mode="before")
    @classmethod
    def _rationale(cls, v: Any) -> str:
        return _plain(str(v), 300) if isinstance(v, str) else ""


def _plain(s: str, limit: int) -> str:
    """Model text shown to a reviewer: no control characters, no markup brackets, bounded."""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s)
    s = s.replace("<", "").replace(">", "")
    return " ".join(s.split())[:limit]


def parse_assessment(raw: str) -> Assessment:
    """Raw model text -> Assessment, or ProviderError('bad_response')."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ProviderError("bad_response", "The model returned no JSON object.")
    try:
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return Assessment.model_validate(data)
    except (ValueError, ValidationError) as exc:
        raise ProviderError("bad_response", "The model returned JSON that does not match the schema.") from exc


@dataclass(frozen=True)
class TextRequest:
    kind: str  # bio | app_description
    text: str  # candidate text: UNTRUSTED
    brand_description: str | None = None  # the brand's own description (trusted context; the only brand data ever sent)
    platform: str | None = None


@dataclass
class TextFinding:
    provider: str
    provenance: str  # api_generated_assessment | deterministic_similarity
    status: str  # ok | unavailable
    model: str | None = None
    scam_likelihood: float | None = None
    categories: list[str] = field(default_factory=list)
    impersonation_claims: list[str] = field(default_factory=list)
    rationale: str = ""
    similarity: dict[str, Any] | None = None  # local baseline only
    reason: str | None = None  # why unavailable
    message: str = ""
    cached: bool = False
    prompt_version: str | None = None
    checked_at: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "provenance": self.provenance, "status": self.status, "model": self.model,
            "scam_likelihood": self.scam_likelihood, "categories": self.categories, "impersonation_claims": self.impersonation_claims,
            "rationale": self.rationale, "similarity": self.similarity, "reason": self.reason, "message": self.message,
            "cached": self.cached, "prompt_version": self.prompt_version, "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    label: str
    configured: bool
    model: str | None = None
    reason: str | None = None  # why not configured (never contains a key)
    needs_key: bool = True

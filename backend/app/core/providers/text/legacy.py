"""Adapter: any object with `assess(text) -> LLMIntentResult` (the older `ScamIntentLLM` protocol, also used by test fakes) as a TextProvider."""

from __future__ import annotations

from typing import Any

from app.config.time import iso_z, utcnow

from .base import classify_exception
from .prompt import PROMPT_VERSION, sanitize
from .schema import API_ASSESSMENT, ProviderStatus, TextFinding, TextRequest


class LegacyTextProvider:
    def __init__(self, llm: Any, name: str = "custom") -> None:
        self.llm, self.name, self.label = llm, name, "Custom provider"

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.name, self.label, True, None, needs_key=False)

    def analyze(self, req: TextRequest) -> TextFinding:
        try:
            r = self.llm.assess(sanitize(req.text))
        except Exception as exc:  # noqa: BLE001
            err = classify_exception(exc)
            return TextFinding(self.name, API_ASSESSMENT, "unavailable", reason=err.reason, message=err.message, prompt_version=PROMPT_VERSION)
        return TextFinding(self.name, API_ASSESSMENT, "ok", None, round(float(r.scam_likelihood), 3), list(r.categories), [], r.rationale,
                           prompt_version=PROMPT_VERSION, checked_at=iso_z(utcnow()))

"""Text analysis for scans: the local baseline always runs; one hosted provider (Gemini by default, Hugging Face optional) runs only when
the master switch SCAM_LLM_ENABLED is on and the provider is configured. Never for sample data. Findings carry their provenance and
live in detection `details` (signals + text_analysis), never in `evidence`.

Only an API finding with scam_likelihood >= 0.6 feeds the unchanged scorer (existing `llm` input: +round(10 x likelihood), inside the
bio cap of 28), so an answer can only ADD capped points: it cannot lower a score, change an allowlist match or confirm anything."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config.config import Settings
from app.core.engine.llm import LLMIntentResult
from app.core.providers.text import (
    ALL_CHOICES,
    API_ASSESSMENT,
    DbCache,
    LocalTextProvider,
    TextFinding,
    TextRequest,
    build_provider,
)
from app.core.providers.text.legacy import LegacyTextProvider
from app.services import app_settings

SETTING_KEY = "text_provider"
QUIET_REASONS = frozenset({"no_key", "no_model", "no_text", "disabled", "not_installed"})


def active_choice(db: Session, settings: Settings) -> str:
    stored = app_settings.get_raw(db, SETTING_KEY)
    choice = stored if isinstance(stored, str) else settings.scam_llm_provider
    return choice if choice in ALL_CHOICES else "gemini"


@dataclass
class TextResult:
    local: TextFinding | None = None
    api: TextFinding | None = None
    llm: LLMIntentResult | None = None
    signals: list[dict] = field(default_factory=list)

    def details(self) -> dict:
        return {"local": self.local.to_dict() if self.local else None, "api": self.api.to_dict() if self.api else None}


class TextAnalysisService:
    def __init__(self, settings: Settings, active: str, api, legacy=None) -> None:
        self.settings, self.active, self.api, self.legacy = settings, active, api, legacy
        self.local = LocalTextProvider()
        self.enabled = bool(settings.scam_llm_enabled or legacy is not None)

    @property
    def note(self) -> str | None:
        """A scan-level warning when the AI check is switched on but cannot run."""
        if self.legacy is not None or not self.enabled or self.active in ("off", "local") or self.api is None:
            return None
        st = self.api.status()
        if not st.configured:
            what = {"no_key": "its API key is not set", "no_model": "no model id is set"}.get(st.reason or "", st.reason)
            return f"Optional AI text check is enabled but {st.label} is not usable ({what}): skipped."
        return None

    def analyze(self, kind: str, text: str, brand_description: str | None, *, synthetic: bool = False, stale: bool = False) -> TextResult:
        req = TextRequest(kind, text, brand_description)
        res = TextResult(local=self.local.analyze(req))
        if res.local.ok:
            res.signals.append({"id": "text_similarity", "status": "present" if (res.local.similarity or {}).get("scam_phrase_points") else "clean",
                                "value": None, "points": 0, "max_points": 0, "provenance": "local_algorithm",
                                "label": "Deterministic similarity (RapidFuzz and phrase rules)", "note": res.local.rationale, "checked_at": res.local.checked_at})
        provider = self.legacy or self.api
        if synthetic or stale or provider is None or self.active in ("off", "local") and self.legacy is None:
            return res
        if not self.enabled:
            res.signals.append(self._unavailable("disabled", "The AI text check is switched off (SCAM_LLM_ENABLED)."))
            return res
        res.api = provider.analyze(req)
        f = res.api
        if f.ok and f.scam_likelihood is not None:
            res.signals.append({"id": "ai_text_assessment", "status": "present" if f.scam_likelihood >= 0.6 else "clean", "value": f.scam_likelihood,
                                "points": 0, "max_points": 0, "provenance": "real_api", "label": "API-generated assessment (an opinion, not evidence)",
                                "note": f"{f.provider}: {f.rationale}"[:300], "checked_at": f.checked_at})
            res.llm = LLMIntentResult(f.scam_likelihood, list(f.categories), f.rationale)
        else:
            res.signals.append(self._unavailable(f.reason or "error", f.message))
        return res

    @staticmethod
    def _unavailable(reason: str, message: str) -> dict:
        return {"id": "ai_text_assessment", "status": "unavailable", "value": None, "points": 0, "max_points": 0, "provenance": "real_api",
                "label": "API-generated assessment (an opinion, not evidence)", "note": message, "checked_at": None, "reason": reason}




def build_text_service(ctx, db: Session) -> TextAnalysisService:
    """Per scan; the provider objects (cool-down state) are kept on the context."""
    s = ctx.settings
    legacy = LegacyTextProvider(ctx.llm) if getattr(ctx, "llm", None) is not None else None
    choice = active_choice(db, s)
    api = None
    if choice in ("gemini", "huggingface"):
        api = ctx.text_providers.get(choice)
        if api is None:
            api = ctx.text_providers[choice] = build_provider(choice, s, DbCache(ctx.session_factory))
    return TextAnalysisService(s, choice, api, legacy)


__all__ = ["API_ASSESSMENT", "TextAnalysisService", "TextResult", "active_choice", "build_text_service"]

"""Build text providers from Settings. Secrets are read here and handed to the provider; they never appear in a status or a log."""

from __future__ import annotations

from typing import Any

from app.config.config import Settings

from .base import TextProvider
from .cache import AnswerCache
from .gemini import GeminiEmbedder, GeminiTextProvider
from .huggingface import HuggingFaceTextProvider
from .local import LocalTextProvider

PROVIDER_NAMES = ("local", "gemini", "huggingface")
API_PROVIDERS = ("gemini", "huggingface")
ALL_CHOICES = (*PROVIDER_NAMES, "off")


def _secret(s: Any) -> str | None:
    return s.get_secret_value() if s else None


def build_provider(name: str, settings: Settings, cache: AnswerCache | None = None, **kw: Any) -> TextProvider:
    common: dict[str, Any] = {"timeout": settings.text_provider_timeout_seconds, "max_retries": settings.text_provider_max_retries,
                              "cache": cache, **kw}
    if name == "gemini":
        return GeminiTextProvider(api_key=_secret(settings.gemini_api_key), model=settings.gemini_model, **common)
    if name == "huggingface":
        return HuggingFaceTextProvider(token=_secret(settings.huggingface_api_token), model=settings.huggingface_model, **common)
    return LocalTextProvider()


def build_embedder(settings: Settings) -> GeminiEmbedder | None:
    """Embeddings are OFF unless TEXT_EMBEDDINGS_ENABLED=true AND a model id + key are set."""
    if not settings.text_embeddings_enabled:
        return None
    e = GeminiEmbedder(_secret(settings.gemini_api_key), settings.gemini_embedding_model, timeout=settings.text_provider_timeout_seconds)
    return e if e.configured else None

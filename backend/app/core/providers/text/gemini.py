"""Gemini text provider through the official `google-genai` SDK (lazy import; key from GEMINI_API_KEY, model from GEMINI_MODEL).

Structured output: `response_mime_type="application/json"` + `response_schema=GeminiResponseSchema`; the answer is then validated again
by `Assessment`. No tools, no grounding, no function calling is configured. Temperature 0, small output cap, per-request timeout."""

from __future__ import annotations

from typing import Any

from .base import ApiTextProvider
from .schema import GeminiResponseSchema, ProviderError

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"  # the plan's recommendation (docs/EXTENSION_PLAN.md); always overridable by GEMINI_MODEL


class GeminiTextProvider(ApiTextProvider):
    name = "gemini"
    label = "Google Gemini"
    default_model = DEFAULT_GEMINI_MODEL

    def __init__(self, api_key: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self._key = api_key

    def _missing(self) -> str | None:
        if self._client is None and not self._key:
            return "no_key"
        return None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai  # lazy: optional dependency
            except ImportError as exc:
                raise ProviderError("not_installed", "The 'google-genai' package is not installed (pip install google-genai).") from exc
            self._client = genai.Client(api_key=self._key, http_options={"timeout": int(self.timeout * 1000)})
        return self._client

    def _call(self, system: str, user: str) -> str:
        client = self._get_client()
        config = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "response_schema": GeminiResponseSchema,
            "temperature": 0,
            "max_output_tokens": 400,
        }
        resp = client.models.generate_content(model=self.model, contents=user, config=config)
        text = getattr(resp, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("bad_response", "Gemini returned no text (the answer may have been blocked).")
        return text


class GeminiEmbedder:
    """OPTIONAL and OFF by default (TEXT_EMBEDDINGS_ENABLED + GEMINI_EMBEDDING_MODEL). The embedding model id is unverified in the plan,
    so there is no default id: the operator sets one that their key can use. Used only for an informational similarity signal."""

    def __init__(self, api_key: str | None, model: str | None, *, client: Any = None, timeout: float = 10.0) -> None:
        self._key, self.model, self._client, self.timeout = api_key, model, client, timeout

    @property
    def configured(self) -> bool:
        return bool(self.model) and (self._client is not None or bool(self._key))

    def embed(self, text: str) -> list[float]:
        if not self.configured:
            raise ProviderError("not_configured", "Embeddings need GEMINI_API_KEY and GEMINI_EMBEDDING_MODEL.")
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise ProviderError("not_installed", "The 'google-genai' package is not installed.") from exc
            self._client = genai.Client(api_key=self._key, http_options={"timeout": int(self.timeout * 1000)})
        resp = self._client.models.embed_content(model=self.model, contents=text)
        embs = getattr(resp, "embeddings", None) or []
        values = getattr(embs[0], "values", None) if embs else None
        if not values:
            raise ProviderError("bad_response", "The embedding answer was empty.")
        return [float(v) for v in values]

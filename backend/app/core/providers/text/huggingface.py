"""Hugging Face Inference Providers through `huggingface_hub.InferenceClient.chat_completion` (lazy import).

Optional. No default model: unavailable until HUGGINGFACE_API_TOKEN and HUGGINGFACE_MODEL are both set (the user must pick a model a
provider currently serves). JSON-schema output is requested; if the provider rejects `response_format` the call is repeated once without
it and the same validation applies."""

from __future__ import annotations

from typing import Any

from .base import ApiTextProvider, classify_exception
from .schema import GeminiResponseSchema, ProviderError


class HuggingFaceTextProvider(ApiTextProvider):
    name = "huggingface"
    label = "Hugging Face Inference Providers"
    default_model = None
    needs_model = True

    def __init__(self, token: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self._token = token

    def _missing(self) -> str | None:
        if self._client is None and not self._token:
            return "no_key"
        if not self.model:
            return "no_model"
        return None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from huggingface_hub import InferenceClient  # lazy: optional dependency
            except ImportError as exc:
                raise ProviderError("not_installed", "The 'huggingface_hub' package is not installed (pip install huggingface_hub).") from exc
            self._client = InferenceClient(model=self.model, token=self._token, timeout=self.timeout)
        return self._client

    def _chat(self, client: Any, messages: list[dict], response_format: dict | None) -> str:
        kwargs: dict[str, Any] = {"messages": messages, "model": self.model, "max_tokens": 400, "temperature": 0}
        if response_format is not None:
            kwargs["response_format"] = response_format
        out = client.chat_completion(**kwargs)
        try:
            content = out.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise ProviderError("bad_response", "Hugging Face returned an unexpected response shape.") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("bad_response", "Hugging Face returned no text.")
        return content

    def _call(self, system: str, user: str) -> str:
        client = self._get_client()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        fmt = {"type": "json_schema", "json_schema": {"name": "assessment", "schema": GeminiResponseSchema.model_json_schema(), "strict": True}}
        try:
            return self._chat(client, messages, fmt)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            if classify_exception(exc).reason != "bad_request":
                raise
        return self._chat(client, messages, None)  # the model/provider does not support structured output: ask in plain text

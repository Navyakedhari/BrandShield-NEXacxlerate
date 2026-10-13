"""Shared machinery for hosted text-analysis providers: validation, timeouts, bounded retries with backoff, cache, quota cool-down.

A provider's `analyze()` NEVER raises: a problem becomes a finding with status `unavailable` and a reason, so a missing key, a
quota error or a malformed answer can never fail a scan. `unavailable` is not `clean`."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from app.config.time import iso_z, utcnow

from .cache import AnswerCache, cache_key
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_user_content, sanitize
from .schema import API_ASSESSMENT, ProviderError, ProviderStatus, TextFinding, TextRequest, parse_assessment

log = logging.getLogger("app.text_provider")

MIN_TEXT_CHARS = 12
QUOTA_COOLDOWN_SECONDS = 600.0
KEY_COOLDOWN_SECONDS = 900.0


class TextProvider(Protocol):
    name: str
    label: str

    def status(self) -> ProviderStatus: ...
    def analyze(self, req: TextRequest) -> TextFinding: ...


def classify_exception(exc: Exception) -> ProviderError:
    """Map any SDK / HTTP exception to a ProviderError without depending on a particular SDK."""
    if isinstance(exc, ProviderError):
        return exc
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = getattr(exc, "status_code", None)
    if not isinstance(code, int):
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
    retry_after: float | None = None
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        if headers and headers.get("retry-after"):
            retry_after = min(float(headers["retry-after"]), 3600.0)
    except (TypeError, ValueError):
        retry_after = None
    name = type(exc).__name__.lower()
    if code == 429:
        return ProviderError("quota", "The provider's rate limit or quota was reached.", retry_after=retry_after)
    if code in (401, 403):
        return ProviderError("invalid_key", "The provider rejected the credentials (check the key and its permissions).")
    if code in (400, 404, 422):
        return ProviderError("bad_request", f"The provider rejected the request (HTTP {code}); check the model id.")
    if isinstance(code, int) and code >= 500:
        return ProviderError("server_error", f"The provider had a server error (HTTP {code}).", retryable=True)
    if "timeout" in name or isinstance(exc, TimeoutError):
        return ProviderError("timeout", "The provider did not answer in time.", retryable=True)
    if any(t in name for t in ("connect", "network", "transport", "remoteprotocol", "connection")):
        return ProviderError("unreachable", "The provider could not be reached.", retryable=True)
    return ProviderError("error", f"The provider call failed ({type(exc).__name__}).")


class ApiTextProvider:
    """Template: subclasses implement `_call` (one HTTP/SDK request, returns the raw model text) and `_missing`."""

    name = "api"
    label = "API"
    default_model: str | None = None
    needs_model = False

    def __init__(self, *, model: str | None = None, timeout: float = 10.0, max_retries: int = 2, backoff: float = 0.5,
                 cache: AnswerCache | None = None, client: Any = None, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.model = model or self.default_model
        self.timeout = timeout
        self.max_retries = max(0, min(max_retries, 3))
        self.backoff = backoff
        self.cache = cache
        self._client = client
        self._sleep = sleep
        self._mono = monotonic
        self._blocked_until = 0.0
        self._blocked_reason: tuple[str, str] | None = None

    # -- to implement ------------------------------------------------------------------------------
    def _missing(self) -> str | None:
        """Reason code when the provider is not configured (no key / no model), else None."""
        raise NotImplementedError

    def _call(self, system: str, user: str) -> str:
        raise NotImplementedError

    # -- public ------------------------------------------------------------------------------------
    def status(self) -> ProviderStatus:
        missing = self._missing()
        msg = {"no_key": "No API key set.", "no_model": "No model id set."}.get(missing or "", missing)
        return ProviderStatus(self.name, self.label, missing is None, self.model, msg)

    def _unavailable(self, reason: str, message: str) -> TextFinding:
        return TextFinding(self.name, API_ASSESSMENT, "unavailable", self.model, reason=reason, message=message, prompt_version=PROMPT_VERSION)

    def analyze(self, req: TextRequest) -> TextFinding:
        missing = self._missing()
        if missing:
            msg = {"no_key": f"{self.label} has no API key configured.", "no_model": f"{self.label} has no model id configured."}.get(missing, missing)
            return self._unavailable(missing, msg)
        text = sanitize(req.text)
        if len(text) < MIN_TEXT_CHARS:
            return self._unavailable("no_text", "There was too little text to assess.")
        if self._mono() < self._blocked_until and self._blocked_reason:
            return self._unavailable(*self._blocked_reason)
        model = self.model or ""
        context = sanitize(req.brand_description, 800)
        key = cache_key(model, PROMPT_VERSION, req.kind, context, text)
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return TextFinding(self.name, API_ASSESSMENT, "ok", model, hit["scam_likelihood"], list(hit["categories"]),
                                   list(hit["impersonation_claims"]), hit["rationale"], cached=True, prompt_version=PROMPT_VERSION,
                                   checked_at=hit.get("checked_at"))
        user = build_user_content(req.kind, text, req.brand_description)
        try:
            raw = self._with_retries(user)
            result = parse_assessment(raw)
        except ProviderError as exc:
            self._remember_block(exc)
            log.warning("text provider %s unavailable: %s", self.name, exc.reason)
            return self._unavailable(exc.reason, exc.message)
        value = {"scam_likelihood": round(result.scam_likelihood, 3), "categories": result.categories,
                 "impersonation_claims": result.impersonation_claims, "rationale": result.rationale, "checked_at": iso_z(utcnow())}
        if self.cache is not None:
            self.cache.put(key, self.name, value)
        return TextFinding(self.name, API_ASSESSMENT, "ok", model, value["scam_likelihood"], value["categories"], value["impersonation_claims"],
                           value["rationale"], prompt_version=PROMPT_VERSION, checked_at=value["checked_at"])

    # -- internals ---------------------------------------------------------------------------------
    def _with_retries(self, user: str) -> str:
        attempt = 0
        while True:
            try:
                return self._call(SYSTEM_PROMPT, user)
            except Exception as exc:  # noqa: BLE001 - classified below
                err = classify_exception(exc)
                if not err.retryable or attempt >= self.max_retries:
                    raise err from exc
                self._sleep(self.backoff * (2**attempt))
                attempt += 1

    def _remember_block(self, err: ProviderError) -> None:
        if err.reason == "quota":
            self._blocked_until = self._mono() + (err.retry_after or QUOTA_COOLDOWN_SECONDS)
            self._blocked_reason = ("quota", "The provider's quota was reached; not asking again for a while.")
        elif err.reason == "invalid_key":
            self._blocked_until = self._mono() + KEY_COOLDOWN_SECONDS
            self._blocked_reason = ("invalid_key", err.message)

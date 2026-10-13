"""Optional Sentry error reporting, driven only by environment variables.

* SENTRY_DSN unset                -> nothing happens (no import, no network).
* SENTRY_DSN set, sentry-sdk not installed -> one warning, app continues. Install with `pip install -r requirements-extra.txt`.
* Everything is scrubbed in `before_send` before leaving the process: auth headers and cookies, request bodies, query strings
  with secrets, user identity, local variables, and any configured secret value found in messages.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from types import ModuleType
from typing import Any

from app.config.config import Settings
from app.config.logging import REDACTED, Redactor

log = logging.getLogger("app.observability")

SENSITIVE_KEYS = (
    "authorization", "cookie", "set-cookie", "x-api-key", "api-key", "api_key", "apikey", "password", "passwd", "secret", "token",
    "access_token", "refresh_token", "jwt", "credential", "dsn", "redis_url", "database_url", "email",
)


def _sensitive(key: object) -> bool:
    k = str(key).lower()
    return any(s in k for s in SENSITIVE_KEYS)


def _scrub(value: Any, redactor: Redactor, depth: int = 0) -> Any:
    """Recursively blank sensitive keys and redact secrets inside strings."""
    if depth > 8:
        return REDACTED
    if isinstance(value, dict):
        return {k: (REDACTED if _sensitive(k) else _scrub(v, redactor, depth + 1)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v, redactor, depth + 1) for v in value]
    if isinstance(value, str):
        return redactor.redact(value)
    return value


def make_before_send(secrets: list[str]) -> Callable[[dict, Any], dict | None]:
    redactor = Redactor(secrets)

    def before_send(event: dict, _hint: Any = None) -> dict | None:
        event = dict(event)
        req = event.get("request")
        if isinstance(req, dict):
            req = dict(req)
            req.pop("data", None)  # request bodies can hold passwords, logos, tokens
            req.pop("cookies", None)
            if "headers" in req:
                req["headers"] = _scrub(req["headers"], redactor)
            if req.get("query_string"):
                req["query_string"] = REDACTED  # may carry tokens; the path is enough to debug
            if isinstance(req.get("env"), dict):
                req["env"] = {k: v for k, v in req["env"].items() if k in ("REMOTE_ADDR_MASKED",)}
            event["request"] = _scrub(req, redactor)
        if "user" in event:
            user = event["user"] if isinstance(event["user"], dict) else {}
            event["user"] = {"id": user["id"]} if "id" in user else {}
        for section in ("extra", "contexts", "tags", "breadcrumbs", "exception", "threads", "logentry", "message"):
            if section in event:
                event[section] = _scrub(event[section], redactor)
        # never ship local variables from stack frames
        for exc in (event.get("exception") or {}).get("values", []) if isinstance(event.get("exception"), dict) else []:
            for frame in (exc.get("stacktrace") or {}).get("frames", []):
                frame.pop("vars", None)
        event.pop("server_name", None)
        return event

    return before_send


def init_sentry(settings: Settings, *, importer: Callable[[str], ModuleType] = importlib.import_module) -> bool:
    """Initialise Sentry if configured. Returns True when the SDK was started. Never raises."""
    dsn = settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else None
    if not dsn:
        return False
    try:
        sentry_sdk = importer("sentry_sdk")
    except ImportError:
        log.warning("SENTRY_DSN is set but sentry-sdk is not installed: error reporting is off (pip install -r requirements-extra.txt)")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.sentry_environment or settings.app_env,
            traces_sample_rate=max(0.0, min(1.0, settings.sentry_traces_sample_rate)),
            send_default_pii=False,
            max_request_body_size="never",
            include_local_variables=False,
            before_send=make_before_send(settings.secret_values),
        )
    except Exception as exc:  # noqa: BLE001 - a bad DSN must not stop the app
        log.warning("Sentry could not be started (%s): error reporting is off", type(exc).__name__)
        return False
    log.info("Sentry error reporting enabled")
    return True

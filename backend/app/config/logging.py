"""Structured (JSON) logging with secret redaction."""
import json
import logging
import re
from datetime import UTC, datetime

REDACTED = "[REDACTED]"
_KEY_VALUE = re.compile(
    r"""(?ix)
    (?P<k>["']?(?:password|passwd|secret|token|access_token|refresh_token|authorization|api[_-]?key|jwt_secret)["']?
    \s*[:=]\s*)
    (?P<v>"[^"]*"|'[^']*'|Bearer\s+\S+|[^\s,;&}]+)
    """
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")


class Redactor:
    def __init__(self, secrets: list[str] | None = None) -> None:
        self.secrets = [s for s in (secrets or []) if s and len(s) >= 4]

    def redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, REDACTED)
        text = _KEY_VALUE.sub(lambda m: m.group("k") + REDACTED, text)
        text = _BEARER.sub("Bearer " + REDACTED, text)
        return _JWT.sub(REDACTED, text)


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redactor.redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = self.redactor.redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter(Redactor(secrets)))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

import json
import logging

import pytest

from app.config.logging import REDACTED, JsonFormatter, RedactingFilter, Redactor

SECRET = "super-secret-jwt-value-123456"


@pytest.mark.parametrize(
    "text",
    [
        "password=hunter2hunter2",
        'login {"password": "hunter2hunter2"}',
        "Authorization: Bearer abc.def.ghi",
        "token: abcdef123456",
        "refresh_token=abcdef",
        "api_key=XYZ123",
        "header eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_nature-1",
        f"leaked {SECRET} here",
    ],
)
def test_secrets_are_redacted(text):
    out = Redactor([SECRET]).redact(text)
    assert REDACTED in out
    for leaked in ("hunter2hunter2", "abc.def.ghi", "abcdef123456", "XYZ123", "eyJhbGci", SECRET):
        assert leaked not in out


def test_normal_text_untouched():
    assert Redactor([SECRET]).redact("brand updated for Acme Bank") == "brand updated for Acme Bank"


def test_json_log_line_is_redacted_end_to_end(capsys):
    logger = logging.getLogger("test.redaction")
    logger.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter(Redactor([SECRET])))
    logger.handlers[:] = [handler]
    logger.warning("login failed password=%s secret=%s", "pw12345678", SECRET)
    err = capsys.readouterr().err
    line = json.loads(err.strip().splitlines()[-1])
    assert line["level"] == "WARNING"
    assert "pw12345678" not in err and SECRET not in err
    assert REDACTED in line["message"]


def test_exception_text_is_redacted(capsys):
    logger = logging.getLogger("test.redaction2")
    logger.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter(Redactor([SECRET])))
    logger.handlers[:] = [handler]
    try:
        raise RuntimeError(f"boom {SECRET}")
    except RuntimeError:
        logger.exception("failed")
    assert SECRET not in capsys.readouterr().err

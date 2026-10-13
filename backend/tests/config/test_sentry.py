"""Sentry hook: lazy, optional, scrubbed. A fake module stands in for sentry-sdk (nothing is installed or sent)."""

from __future__ import annotations

import sys
from types import ModuleType

from pydantic import SecretStr

from app.config.observability import init_sentry, make_before_send
from tests.scan_helpers import make_client

DSN = "https://publickey123@o0.ingest.example.invalid/42"


class FakeSentry(ModuleType):
    def __init__(self, fail: bool = False) -> None:
        super().__init__("sentry_sdk")
        self.calls: list[dict] = []
        self.fail = fail

    def init(self, **kw):
        if self.fail:
            raise ValueError("bad dsn " + DSN)
        self.calls.append(kw)


def cfg(settings, **kw):
    return settings.model_copy(update=kw)


def test_no_dsn_is_a_noop_and_never_imports(settings):
    def importer(_name):
        raise AssertionError("sentry_sdk must not be imported without a DSN")

    assert init_sentry(settings, importer=importer) is False


def test_dsn_but_package_missing_is_a_noop(settings, caplog):
    def importer(_name):
        raise ImportError("No module named sentry_sdk")

    assert init_sentry(cfg(settings, sentry_dsn=SecretStr(DSN)), importer=importer) is False
    assert "not installed" in caplog.text and DSN not in caplog.text


def test_init_arguments(settings):
    fake = FakeSentry()
    s = cfg(settings, sentry_dsn=SecretStr(DSN), sentry_environment="staging", sentry_traces_sample_rate=0.25)
    assert init_sentry(s, importer=lambda _n: fake) is True
    kw = fake.calls[0]
    assert kw["dsn"] == DSN and kw["environment"] == "staging" and kw["traces_sample_rate"] == 0.25
    assert kw["send_default_pii"] is False and kw["max_request_body_size"] == "never" and kw["include_local_variables"] is False
    assert callable(kw["before_send"])


def test_environment_defaults_to_app_env_and_rate_is_clamped(settings):
    fake = FakeSentry()
    init_sentry(cfg(settings, sentry_dsn=SecretStr(DSN), sentry_traces_sample_rate=7.0, app_env="production"), importer=lambda _n: fake)
    assert fake.calls[0]["environment"] == "production" and fake.calls[0]["traces_sample_rate"] == 1.0


def test_init_failure_is_swallowed_and_does_not_log_the_dsn(settings, caplog):
    assert init_sentry(cfg(settings, sentry_dsn=SecretStr(DSN)), importer=lambda _n: FakeSentry(fail=True)) is False
    assert DSN not in caplog.text


def test_before_send_scrubs_request_user_and_secrets():
    secret = "jwt-secret-value-0123456789"
    send = make_before_send([secret])
    event = {
        "message": f"failed with {secret}",
        "request": {
            "url": "https://api.example.com/api/auth/login",
            "method": "POST",
            "headers": {"Authorization": "Bearer abc.def.ghi", "Cookie": "sid=1", "X-Api-Key": "k", "User-Agent": "pytest", "Accept": "json"},
            "data": {"email": "a@b.c", "password": "hunter2"},
            "cookies": {"sid": "1"},
            "query_string": "token=abc&page=2",
        },
        "user": {"id": 7, "email": "a@b.c", "ip_address": "1.2.3.4", "username": "alice"},
        "extra": {"api_key": "sk-123", "note": f"has {secret} inside", "nested": {"refresh_token": "r", "ok": 1}},
        "exception": {"values": [{"type": "X", "value": f"boom password=hunter2 {secret}",
                                  "stacktrace": {"frames": [{"function": "f", "vars": {"password": "hunter2"}}]}}]},
        "breadcrumbs": {"values": [{"message": "Authorization: Bearer zzz.yyy.xxx"}]},
        "server_name": "host-1",
    }
    out = send(event, {})
    text = str(out)
    for leak in ("hunter2", secret, "abc.def.ghi", "sid=1", "a@b.c", "1.2.3.4", "alice", "sk-123", "token=abc", "zzz.yyy.xxx", "host-1"):
        assert leak not in text, leak
    assert "data" not in out["request"] and "cookies" not in out["request"]
    assert out["request"]["headers"]["User-Agent"] == "pytest" and out["request"]["headers"]["Accept"] == "json"
    assert out["request"]["url"].endswith("/api/auth/login") and out["request"]["method"] == "POST"
    assert out["user"] == {"id": 7}
    assert out["extra"]["nested"]["ok"] == 1
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "request" in event and "data" in event["request"]  # the original event is not mutated


def test_before_send_handles_sparse_events():
    send = make_before_send([])
    assert send({}, None) == {}
    assert send({"user": "weird"}, None)["user"] == {}


def test_create_app_initialises_sentry_lazily_from_env(settings, checker, monkeypatch):
    fake = FakeSentry()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    with make_client(cfg(settings, sentry_dsn=SecretStr(DSN), sentry_environment="demo"), checker):
        pass
    assert len(fake.calls) == 1 and fake.calls[0]["environment"] == "demo"


def test_create_app_without_dsn_does_not_touch_sentry(settings, checker, monkeypatch):
    fake = FakeSentry()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    with make_client(settings, checker):
        pass
    assert fake.calls == []


def test_dsn_is_treated_as_a_secret_in_logs(settings):
    assert DSN in cfg(settings, sentry_dsn=SecretStr(DSN)).secret_values
    assert DSN not in repr(cfg(settings, sentry_dsn=SecretStr(DSN)))

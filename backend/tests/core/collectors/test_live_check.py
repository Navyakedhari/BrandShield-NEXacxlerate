"""The manual live-check script is never run for real: HTTP is monkeypatched and the key must not be printed."""

import httpx

from app.config.config import Settings
from scripts import live_check

KEY = "tvly-test-key-not-real-0123456789"


def _settings(key):
    return Settings(_env_file=None, database_url="sqlite://", **({"tavily_api_key": key} if key else {}))  # type: ignore[call-arg]


def test_without_key_it_sends_nothing(monkeypatch, capsys):
    monkeypatch.setattr(live_check, "get_settings", lambda: _settings(None))
    assert live_check.main([]) == 2
    assert "not set" in capsys.readouterr().out


def test_with_key_makes_one_post_and_never_prints_the_key(monkeypatch, capsys, tmp_path):
    sent = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append((url, json, headers))
        return httpx.Response(200, json={"results": [{"url": "https://www.instagram.com/x/"}], "usage": {"credits": 1}, "request_id": "r"})

    monkeypatch.setattr(live_check, "get_settings", lambda: _settings(KEY))
    monkeypatch.setattr(live_check.httpx, "post", fake_post)
    out_file = tmp_path / "raw.json"
    assert live_check.main(["--save", str(out_file)]) == 0
    out = capsys.readouterr().out
    assert KEY not in out and "Results returned: 1" in out and "Credits reported by Tavily: 1" in out
    assert len(sent) == 1 and sent[0][1]["search_depth"] == "basic" and sent[0][1]["max_results"] == 3
    assert KEY not in out_file.read_text(encoding="utf-8")


def test_error_status_is_explained(monkeypatch, capsys):
    monkeypatch.setattr(live_check, "get_settings", lambda: _settings(KEY))
    monkeypatch.setattr(live_check.httpx, "post", lambda *a, **k: httpx.Response(432, json={}))
    assert live_check.main([]) == 1
    assert "plan usage limit" in capsys.readouterr().out

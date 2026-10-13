"""Optional screenshot hook (OFF by default; `SCREENSHOTS_ENABLED=true`).

Playwright is NOT installed and NOT in requirements; it is imported lazily inside `capture`. Not tested against
a live browser. The URL goes through the same public-host check as every other fetch before the browser opens it
(note: a browser follows redirects and sub-resources itself, so run it in an isolated sandbox if you turn it on).
"""

from __future__ import annotations

from typing import Protocol

from app.config.config import Settings
from app.core.pipeline.url_safety import UnsafeURLError, validate_public_url


class ScreenshotProvider(Protocol):
    def capture(self, url: str) -> bytes | None:
        """PNG bytes, or None when no screenshot could be taken. May raise; callers treat that as 'no screenshot'."""
        ...


class PlaywrightScreenshotProvider:
    def __init__(self, timeout_ms: int = 15000) -> None:
        self.timeout_ms = timeout_ms

    def capture(self, url: str) -> bytes | None:  # pragma: no cover - needs a real browser
        try:
            validate_public_url(url)
        except UnsafeURLError:
            return None
        from playwright.sync_api import sync_playwright  # lazy: optional package

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                page.goto(url, timeout=self.timeout_ms)
                return page.screenshot(type="png")
            finally:
                browser.close()


def build_screenshot_provider(settings: Settings) -> ScreenshotProvider | None:
    if not settings.screenshots_enabled:
        return None
    return PlaywrightScreenshotProvider()

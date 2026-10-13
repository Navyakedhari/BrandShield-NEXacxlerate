"""Builds the ScanContext (collectors runtime, SSRF-safe fetcher, screenshot hook, job dispatcher)."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.config.config import Settings
from app.core.collectors.selection import CollectorRuntime
from app.core.pipeline.image_fetch import SafeFetcher
from app.core.pipeline.scan_pipeline import ScanContext, run_scan
from app.core.pipeline.screenshot import ScreenshotProvider, build_screenshot_provider
from app.services.jobs import JobDispatcher


def build_context(
    settings: Settings,
    session_factory: sessionmaker[Session],
    runtime: CollectorRuntime,
    *,
    fetcher: SafeFetcher | None = None,
    screenshot: ScreenshotProvider | None = None,
) -> ScanContext:
    ctx = ScanContext(
        settings=settings,
        session_factory=session_factory,
        runtime=runtime,
        fetcher=fetcher
        or SafeFetcher(
            timeout=settings.scan_logo_timeout_seconds,
            max_redirects=settings.url_check_max_redirects,
            max_bytes=settings.scan_logo_max_bytes,
        ),
        screenshot=screenshot if screenshot is not None else build_screenshot_provider(settings),
    )
    ctx.dispatcher = JobDispatcher(settings, lambda scan_id: run_scan(ctx, scan_id))
    return ctx

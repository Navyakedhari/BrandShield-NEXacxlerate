import hashlib
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.api import (
    audit,
    auth,
    brands,
    candidates,
    dashboard,
    detections,
    evaluation,
    health,
    lookalike,
    providers,
    reports,
    scans,
    sources,
)
from app.api import settings as settings_api
from app.config.config import Settings, get_settings
from app.config.errors import install_error_handlers
from app.config.logging import configure_logging
from app.config.observability import init_sentry
from app.config.ratelimit import FailureLimiter, RequestLimiter
from app.core.collectors.selection import CollectorRuntime
from app.core.pipeline.image_fetch import SafeFetcher
from app.core.pipeline.screenshot import ScreenshotProvider
from app.core.pipeline.url_safety import UrlChecker
from app.db.session import make_engine, make_session_factory
from app.services.context import build_context
from app.services.scheduler import SchedulerLoop
from app.services.seed import seed_admin


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    url_checker: UrlChecker | None = None,
    collector_runtime: CollectorRuntime | None = None,
    image_fetcher: SafeFetcher | None = None,
    screenshot_provider: ScreenshotProvider | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.secret_values)
    init_sentry(settings)  # no-op unless SENTRY_DSN is set and sentry-sdk is installed
    engine = engine or make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            import logging

            logging.getLogger("app").error("DATA_DIR cannot be created or is not writable: /health reports data_dir=down")
        try:
            with app.state.session_factory() as db:
                seed_admin(db, settings)
        except SQLAlchemyError:
            # Tables may not exist yet (migrations not applied). /health will report the problem.
            import logging

            logging.getLogger("app").warning("admin seed skipped: database not ready (run alembic upgrade head)")
        loop = None
        # The scheduler loop only runs when enabled, and never under APP_ENV=test.
        if settings.scheduler_enabled and settings.app_env != "test":
            loop = SchedulerLoop(app.state.scan_ctx, settings.scheduler_poll_seconds)
            loop.start()
        app.state.scheduler_loop = loop
        yield
        if loop is not None:
            loop.stop()

    app = FastAPI(title="ScamMind AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.login_limiter = FailureLimiter(settings.login_max_failures, settings.login_window_seconds)
    app.state.request_limiter = RequestLimiter(settings.api_rate_limit_per_minute, 60)
    app.state.url_checker = url_checker or UrlChecker(
        timeout=settings.url_check_timeout_seconds,
        max_redirects=settings.url_check_max_redirects,
        max_bytes=settings.url_check_max_bytes,
    )
    app.state.collector_runtime = collector_runtime or CollectorRuntime(settings, app.state.session_factory)
    app.state.scan_ctx = build_context(
        settings, app.state.session_factory, app.state.collector_runtime, fetcher=image_fetcher, screenshot=screenshot_provider
    )
    app.state.scheduler_loop = None

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        # /health* and CORS preflights are exempt. Both the client IP and (if present) the bearer token are limited,
        # so garbage tokens cannot be used to dodge the per-IP bucket.
        path = request.url.path
        if path.startswith("/api") and request.method != "OPTIONS":
            lim: RequestLimiter = request.app.state.request_limiter
            ip = request.client.host if request.client else "unknown"
            keys = [f"ip:{ip}"]
            auth = request.headers.get("authorization")
            if auth:
                keys.append("tok:" + hashlib.sha256(auth.encode()).hexdigest()[:16])
            if not all([lim.allow(k) for k in keys]):
                return JSONResponse(
                    {"detail": "Too many requests. Slow down and retry shortly."}, status_code=429, headers={"Retry-After": "60"}
                )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)

    api = APIRouter(prefix="/api")
    api.include_router(auth.router)
    api.include_router(brands.router)
    api.include_router(audit.router)
    api.include_router(sources.router)
    for r in (scans, detections, dashboard, lookalike, settings_api, reports, evaluation, candidates, providers):
        api.include_router(r.router)
    api.include_router(candidates.images)
    app.include_router(health.router)
    app.include_router(api)
    return app

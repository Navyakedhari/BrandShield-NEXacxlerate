"""Backend launcher for the frontend Playwright smoke tests. NOT for production.

Safety properties (all asserted below):
- never reads backend/.env or ../.env (Settings(_env_file=None)); every value comes from the process environment
- no Tavily / SearXNG / Gemini / Redis settings are honoured, so data mode `auto` resolves to `sample`
- the reachability check for official accounts is a stub: no outbound HTTP at all
- a temporary SQLite database and data directory are created by the caller (E2E_DB_PATH / E2E_DATA_DIR)

Required env (set by the Playwright config for this process only): JWT_SECRET, ADMIN_EMAIL, ADMIN_PASSWORD,
E2E_DB_PATH, E2E_DATA_DIR, CORS_ORIGINS. Optional: E2E_PORT (default 8765), E2E_VIEWER_EMAIL + E2E_VIEWER_PASSWORD (seeds a viewer).
"""
import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

for name in ("TAVILY_API_KEY", "SEARXNG_BASE_URL", "GEMINI_API_KEY", "REDIS_URL", "SCAN_INTERVAL_MINUTES"):
    os.environ.pop(name, None)  # a developer's shell must never reach the e2e run
os.environ["APP_ENV"] = "test"  # also keeps the in-process scheduler off
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SCAN_EXECUTOR"] = "thread"
os.environ["DATABASE_URL"] = "sqlite:///" + Path(os.environ["E2E_DB_PATH"]).as_posix()
os.environ["DATA_DIR"] = os.environ["E2E_DATA_DIR"]

import uvicorn  # noqa: E402

import app.config.security as security  # noqa: E402
from app.config.config import Settings  # noqa: E402
from app.core.pipeline.url_safety import CheckResult  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import make_engine  # noqa: E402
from app.main import create_app  # noqa: E402

security.BCRYPT_ROUNDS = 4  # faster hashing for a throwaway database


class OfflineChecker:
    """No network: handles containing 'unreachable' report an unreachable URL, everything else is reachable."""

    def check(self, url: str) -> CheckResult:
        if "unreachable" in url:
            return CheckResult("unreachable", "URL could not be reached (stub)", None)
        return CheckResult("verified", "URL is reachable (stub)", 200)


def seed_viewer(engine) -> None:
    """Optional read-only user for the role tests (E2E_VIEWER_EMAIL / E2E_VIEWER_PASSWORD, throwaway values from the test run)."""
    email, password = os.environ.get("E2E_VIEWER_EMAIL"), os.environ.get("E2E_VIEWER_PASSWORD")
    if not (email and password):
        return
    from sqlalchemy import select

    from app.config.security import hash_password
    from app.db.session import make_session_factory
    from app.models import User

    with make_session_factory(engine)() as db:
        if not db.scalar(select(User.id).where(User.email == email.lower())):
            db.add(User(name="E2E Viewer", email=email.lower(), password_hash=hash_password(password), role="viewer"))
            db.commit()


def build():
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.tavily_api_key is None and settings.searxng_base_url is None and settings.redis_url is None
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    seed_viewer(engine)
    return create_app(settings, engine=engine, url_checker=OfflineChecker())  # type: ignore[arg-type]


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=int(os.environ.get("E2E_PORT", "8765")), log_level="warning")

"""Apply database migrations:  python -m scripts.migrate   (run from backend/)

Safe to run from several replicas at once: on PostgreSQL a session-level advisory lock serialises the runs, so the
second one waits, then finds the database already at head and does nothing. SQLite (single process) needs no lock.
Only DATABASE_URL is read (no JWT_SECRET etc. needed).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.config.config import DatabaseSettings
from app.db.session import make_engine

SERVER_DIR = Path(__file__).resolve().parents[1]
LOCK_ID = 727_274_0001  # arbitrary application-wide advisory lock key for "scammind migrations"
log = logging.getLogger("scripts.migrate")


def upgrade(url: str, revision: str = "head") -> None:
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    if not url.startswith("postgresql"):
        command.upgrade(cfg, revision)
        return
    from sqlalchemy import text

    engine = make_engine(url)
    with engine.connect() as lock_conn:  # holds the lock for the whole upgrade; released when the connection closes
        lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_ID})
        try:
            command.upgrade(cfg, revision)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_ID})
            lock_conn.commit()
    engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = DatabaseSettings().database_url
    log.info("applying migrations (alembic upgrade head)")
    upgrade(url)
    log.info("migrations are at head")
    return 0


if __name__ == "__main__":
    sys.exit(main())

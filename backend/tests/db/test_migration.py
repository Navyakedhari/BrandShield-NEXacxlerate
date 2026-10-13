from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

import app.models  # noqa: F401
from alembic import command
from app.db.base import Base
from app.db.session import make_engine

SERVER = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "users", "refresh_tokens", "brands", "official_accounts", "official_apps", "detections",
    "official_matches", "sources", "scans", "settings", "reports", "api_usage", "audit_log", "collector_state",
    "detection_events", "lookalike_cases", "brand_logos", "brand_publishers", "asset_verifications", "candidate_images", "text_analysis_cache",
}


def _cfg(url: str) -> Config:
    cfg = Config(str(SERVER / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_migration_applies_on_sqlite_and_matches_models(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    command.upgrade(_cfg(url), "head")
    engine = make_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migration differ: {diff}"


def test_migration_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{(tmp_path / 'd.db').as_posix()}"
    cfg = _cfg(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert set(inspect(make_engine(url)).get_table_names()) <= {"alembic_version"}


def test_unique_constraints_exist(tmp_path):
    url = f"sqlite:///{(tmp_path / 'u.db').as_posix()}"
    command.upgrade(_cfg(url), "head")
    insp = inspect(make_engine(url))
    cols = [tuple(u["column_names"]) for u in insp.get_unique_constraints("official_accounts")]
    assert ("brand_id", "platform", "handle_key") in cols
    cols = [tuple(u["column_names"]) for u in insp.get_unique_constraints("official_apps")]
    assert ("brand_id", "package_key") in cols

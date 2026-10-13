"""PostgreSQL-only checks. Skipped unless TEST_DATABASE_URL points at a throwaway PostgreSQL database.

Each test migrates inside its own schema (search_path), so nothing outside `t_pg_<random>` schemas is touched.
"""

from __future__ import annotations

import os
import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from alembic import command
from app.db.base import Base
from tests.db.test_migration import EXPECTED_TABLES, _cfg

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set (PostgreSQL not available)")


@pytest.fixture
def pg():
    schema = f"t_pg_{uuid.uuid4().hex[:10]}"
    admin = create_engine(URL, poolclass=NullPool)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = make_url(URL).update_query_dict({"options": f"-csearch_path={schema}"})
    url = scoped.render_as_string(hide_password=False)
    yield url, create_engine(url, poolclass=NullPool)
    with admin.begin() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_migrations_apply_match_models_and_downgrade(pg):
    url, eng = pg
    cfg = _cfg(url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    assert EXPECTED_TABLES <= set(inspect(eng).get_table_names())
    with eng.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0007"
    command.downgrade(cfg, "base")
    assert set(inspect(eng).get_table_names()) <= {"alembic_version"}


def _ins(c, sql: str, **p):
    c.execute(text(sql), p)


def test_partial_unique_indexes_enforced_on_postgres(pg):
    url, eng = pg
    command.upgrade(_cfg(url.replace("%", "%%")), "head")
    now = "2026-01-01T00:00:00Z"
    with eng.begin() as c:
        _ins(c, "INSERT INTO brands (id, name, industry, aliases, keywords, domains, source, created_at, updated_at) "
                "VALUES (1, 'A', '', '[]', '[]', '[]', 'live', :n, :n)", n=now)
        for job, st in (("j1", "running"), ("j2", "completed"), ("j3", "failed")):
            _ins(c, "INSERT INTO scans (job_id, brand_id, scope, state, progress, message, steps, data_mode, started_by, counts, source_report, "
                    "timings, errors, created_at) VALUES (:j, 1, 'all', :s, 0, '', '[]', 'live', 'x', '{}', '[]', '{}', '[]', :n)", j=job, s=st, n=now)
    with pytest.raises(IntegrityError), eng.begin() as c:
        _ins(c, "INSERT INTO scans (job_id, brand_id, scope, state, progress, message, steps, data_mode, started_by, counts, source_report, "
                "timings, errors, created_at) VALUES ('j4', 1, 'all', 'queued', 0, '', '[]', 'live', 'x', '{}', '[]', '{}', '[]', :n)", n=now)
    with eng.begin() as c:
        for b in (True, False, False):
            _ins(c, "INSERT INTO lookalike_cases (name, expected, builtin, source, created_at) VALUES ('n', 'flagged', :b, 'live', :n)", b=b, n=now)
    with pytest.raises(IntegrityError), eng.begin() as c:
        _ins(c, "INSERT INTO lookalike_cases (name, expected, builtin, source, created_at) VALUES ('n', 'flagged', true, 'live', :n)", n=now)


def test_0004_cleans_duplicates_on_postgres(pg):
    url, eng = pg
    cfg = _cfg(url.replace("%", "%%"))
    command.upgrade(cfg, "0003")
    now = "2026-01-01T00:00:00Z"
    with eng.begin() as c:
        _ins(c, "INSERT INTO brands (id, name, industry, aliases, keywords, domains, source, created_at, updated_at) "
                "VALUES (1, 'A', '', '[]', '[]', '[]', 'live', :n, :n)", n=now)
        for job, st in (("j1", "queued"), ("j2", "running")):
            _ins(c, "INSERT INTO scans (job_id, brand_id, scope, state, progress, message, steps, data_mode, started_by, counts, source_report, "
                    "timings, errors, created_at) VALUES (:j, 1, 'all', :s, 0, '', '[]', 'live', 'x', '{}', '[]', '{}', '[]', :n)", j=job, s=st, n=now)
        for _ in range(3):
            _ins(c, "INSERT INTO lookalike_cases (name, expected, builtin, source, created_at) VALUES ('dup', 'flagged', true, 'live', :n)", n=now)
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        assert dict(c.execute(text("SELECT job_id, state FROM scans")).fetchall()) == {"j1": "failed", "j2": "running"}
        assert c.execute(text("SELECT count(*) FROM lookalike_cases")).scalar() == 1


def test_json_columns_round_trip(pg):
    from sqlalchemy.orm import sessionmaker

    from app.models import Brand, Detection

    url, eng = pg
    Base.metadata.create_all(eng)
    with sessionmaker(eng)() as db:
        b = Brand(name="J", aliases=["a", "b"], keywords=[], domains=["x.com"])
        db.add(b)
        db.flush()
        db.add(Detection(brand_id=b.id, type="fake_profile", category="social", platform="x", score=50, band="low", canonical_key="x:y",
                         reasons=[{"signal": "s", "points": 1}], evidence={"sha256": "ab"}, details={"k": [1, 2]}))
        db.commit()
        got = db.query(Detection).one()
        assert got.reasons == [{"signal": "s", "points": 1}] and got.details == {"k": [1, 2]}
        assert db.query(Brand).one().aliases == ["a", "b"]


def test_concurrent_migrate_runs_are_serialised_by_the_advisory_lock(pg):
    """Several replicas start together: one migrates, the others wait and then find the database at head."""
    import threading

    from scripts.migrate import upgrade

    url, eng = pg
    errors: list[Exception] = []

    def run() -> None:
        try:
            upgrade(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors, errors
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0007"

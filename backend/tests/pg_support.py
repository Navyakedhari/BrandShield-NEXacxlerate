"""Run the test suite on PostgreSQL when TEST_DATABASE_URL is set (otherwise a no-op).

Each `make_engine("sqlite://")` call in a test becomes an engine bound to its own freshly created schema (search_path), so tests stay
isolated exactly as with in-memory SQLite. All schemas are dropped at interpreter exit. TEST_DATABASE_URL must point at a throwaway
database: the helper only ever creates and drops schemas named `t_<random>`, but it still needs CREATE rights.
"""

from __future__ import annotations

import atexit
import os
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

URL_ENV = "TEST_DATABASE_URL"
_schemas: list[str] = []


def enabled() -> bool:
    return bool(os.environ.get(URL_ENV))


def install(session_module) -> None:
    if not enabled():
        return
    url = os.environ[URL_ENV]
    original = session_module.make_engine
    admin = create_engine(url, poolclass=NullPool)

    def make_engine(db_url: str):
        if db_url != "sqlite://":
            return original(db_url)
        schema = f"t_{uuid.uuid4().hex[:12]}"
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        _schemas.append(schema)
        return create_engine(url, poolclass=NullPool, connect_args={"options": f"-c search_path={schema}"})

    session_module.make_engine = make_engine

    def cleanup() -> None:
        for s in _schemas:  # one transaction per schema: a single big one exhausts max_locks_per_transaction
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{s}" CASCADE'))

    atexit.register(cleanup)

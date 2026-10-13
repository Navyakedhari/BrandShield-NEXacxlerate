"""Migration 0004: partial unique indexes (one active scan per brand, built-in look-alike cases seeded once). SQLite here; see test_postgres.py."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.core.pipeline import lookalike
from app.core.pipeline import scan_pipeline as sp
from app.db.session import make_engine, make_session_factory
from app.models import Brand, LookalikeCase, Scan
from tests.db.test_migration import _cfg
from tests.scan_helpers import make_client, run_scan, setup_brand


def _fill_row(conn, table: str, **given) -> None:
    """Insert one row into `table`, giving every other NOT NULL column a harmless value (raw SQL, schema as of the current revision)."""
    cols = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    values = dict(given)
    for _cid, name, typ, notnull, default, pk in cols:
        if name in values or (pk and name == "id") or not notnull or default is not None:
            continue
        t = (typ or "").upper()
        values[name] = 0 if ("INT" in t or "BOOL" in t) else ("2026-01-01 00:00:00" if "DATE" in t else ("{}" if "JSON" in t else "x"))
    names = ", ".join(values)
    conn.execute(text(f"INSERT INTO {table} ({names}) VALUES ({', '.join(':' + k for k in values)})"), values)


@pytest.fixture
def migrated(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm4.db').as_posix()}"
    command.upgrade(_cfg(url), "head")
    return url


def test_indexes_exist_and_are_partial_unique(migrated):
    insp = inspect(make_engine(migrated))
    scans = {i["name"]: i for i in insp.get_indexes("scans")}
    cases = {i["name"]: i for i in insp.get_indexes("lookalike_cases")}
    assert scans["uq_scans_one_active_per_brand"]["unique"] and scans["uq_scans_one_active_per_brand"]["column_names"] == ["brand_id"]
    assert cases["uq_lookalike_cases_builtin_name"]["unique"] and cases["uq_lookalike_cases_builtin_name"]["column_names"] == ["name"]
    assert "queued" in str(scans["uq_scans_one_active_per_brand"]["dialect_options"]["sqlite_where"])


def test_only_one_active_scan_per_brand(migrated):
    eng = make_engine(migrated)
    with eng.begin() as c:
        _fill_row(c, "brands", id=1, name="A")
        _fill_row(c, "brands", id=2, name="B")
        _fill_row(c, "scans", brand_id=1, job_id="j1", state="running")
        _fill_row(c, "scans", brand_id=2, job_id="j2", state="queued")  # another brand: fine
        _fill_row(c, "scans", brand_id=1, job_id="j3", state="completed")  # finished scans are unlimited
        _fill_row(c, "scans", brand_id=1, job_id="j4", state="failed")
    with pytest.raises(IntegrityError), eng.begin() as c:
        _fill_row(c, "scans", brand_id=1, job_id="j5", state="queued")
    with eng.begin() as c:  # once the running one finishes, a new one may start
        c.execute(text("UPDATE scans SET state = 'completed' WHERE job_id = 'j1'"))
        _fill_row(c, "scans", brand_id=1, job_id="j6", state="queued")


def test_builtin_case_names_are_unique_but_custom_ones_may_repeat(migrated):
    eng = make_engine(migrated)
    with eng.begin() as c:
        _fill_row(c, "lookalike_cases", name="acrnebank", expected="flagged", builtin=1)
        _fill_row(c, "lookalike_cases", name="my case", expected="flagged", builtin=0)
        _fill_row(c, "lookalike_cases", name="my case", expected="flagged", builtin=0)
        _fill_row(c, "lookalike_cases", name="acrnebank", expected="flagged", builtin=0)  # a custom copy of a built-in name is allowed
    with pytest.raises(IntegrityError), eng.begin() as c:
        _fill_row(c, "lookalike_cases", name="acrnebank", expected="flagged", builtin=1)


def test_upgrade_cleans_existing_duplicates(tmp_path):
    url = f"sqlite:///{(tmp_path / 'dup.db').as_posix()}"
    cfg = _cfg(url)
    command.upgrade(cfg, "0003")
    eng = make_engine(url)
    with eng.begin() as c:
        _fill_row(c, "brands", id=1, name="A")
        for i, st in enumerate(("queued", "running", "completed", "queued")):  # three active scans for one brand (the race)
            _fill_row(c, "scans", brand_id=1, job_id=f"j{i}", state=st)
        for _ in range(2):  # twice-seeded built-in cases (the Phase 5 race)
            _fill_row(c, "lookalike_cases", name="acrnebank", expected="flagged", builtin=1)
            _fill_row(c, "lookalike_cases", name="Acme Bank", expected="official", builtin=1)
        _fill_row(c, "lookalike_cases", name="mine", expected="flagged", builtin=0)
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        states = dict(c.execute(text("SELECT job_id, state FROM scans")).fetchall())
        assert states == {"j0": "failed", "j1": "failed", "j2": "completed", "j3": "queued"}  # newest active one survives
        assert c.execute(text("SELECT count(*) FROM lookalike_cases WHERE builtin = 1")).scalar() == 2
        assert c.execute(text("SELECT count(*) FROM lookalike_cases")).scalar() == 3


def test_downgrade_removes_indexes(tmp_path):
    url = f"sqlite:///{(tmp_path / 'dg.db').as_posix()}"
    cfg = _cfg(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0003")
    insp = inspect(make_engine(url))
    assert "uq_scans_one_active_per_brand" not in {i["name"] for i in insp.get_indexes("scans")}
    assert "uq_lookalike_cases_builtin_name" not in {i["name"] for i in insp.get_indexes("lookalike_cases")}


def test_sqlite_driver_present():
    assert sqlite3.sqlite_version_info >= (3, 15)  # partial indexes need 3.8; row values 3.15


# ------------------------------------------------------------------------------------------------ application behaviour


def test_ensure_default_cases_survives_a_second_seeder(tmp_path):
    """Another process seeded between our check and our insert: the index rejects ours and we carry on."""
    from app.db.base import Base

    eng = make_engine(f"sqlite:///{(tmp_path / 'seed.db').as_posix()}")
    Base.metadata.create_all(eng)
    sf = make_session_factory(eng)
    with sf() as first:
        lookalike.ensure_default_cases(first)
    with sf() as second:
        second.scalar = lambda *a, **k: None  # type: ignore[method-assign]  # pretends the table was empty when it checked
        lookalike.ensure_default_cases(second)  # must not raise
    with sf() as db:
        assert db.query(LookalikeCase).count() == 12


def test_scan_start_race_returns_409_not_500(settings, checker, monkeypatch):
    """Two API processes both see 'no active scan'; the database lets only one insert win and the loser gets the normal 409."""
    with make_client(settings, checker, collectors=[]) as c:
        from tests.conftest import headers_for

        h = headers_for(c, "admin@example.com", "Admin-Pass-for-tests-1")
        setup_brand(c, h)
        with c.app_ref.state.session_factory() as db:
            brand = db.query(Brand).one()
            from app.config.time import utcnow

            db.add(Scan(job_id="job_existing", brand_id=brand.id, state="running", started_at=utcnow(), created_at=utcnow()))
            db.commit()
        real = sp.running_scan
        calls = {"n": 0}

        def blind_first(db, brand_id):
            calls["n"] += 1
            return None if calls["n"] == 1 else real(db, brand_id)

        monkeypatch.setattr(sp, "running_scan", blind_first)
        r = c.post("/api/scan", json={"scope": "all"}, headers=h)
        assert r.status_code == 409 and r.json()["job_id"] == "job_existing"
        monkeypatch.undo()
        with c.app_ref.state.session_factory() as db:
            assert db.query(Scan).count() == 1  # the loser left nothing behind


def test_normal_scans_still_run_back_to_back(client, admin_h):
    setup_brand(client, admin_h)
    assert run_scan(client, admin_h)["status"] in ("completed", "partial")
    assert run_scan(client, admin_h)["status"] in ("completed", "partial")


def test_migrate_script_is_idempotent_on_sqlite(tmp_path):
    from scripts.migrate import upgrade

    url = f"sqlite:///{(tmp_path / 'script.db').as_posix()}"
    upgrade(url)
    upgrade(url)  # second run is a no-op
    with make_engine(url).connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0007"

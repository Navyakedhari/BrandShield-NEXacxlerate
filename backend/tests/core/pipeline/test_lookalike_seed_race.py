"""Two requests that arrive together must not seed the default look-alike cases twice (found by the Phase 5 browser tests)."""
import threading

from sqlalchemy import func, select

from app.core.pipeline import lookalike
from app.db.base import Base
from app.db.session import make_engine, make_session_factory
from app.models import LookalikeCase


def test_concurrent_first_use_seeds_exactly_twelve_cases(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    barrier = threading.Barrier(6)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            with factory() as db:
                barrier.wait(timeout=5)
                lookalike.ensure_default_cases(db)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not errors
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(LookalikeCase)) == len(lookalike.DEFAULT_CASES) == 12

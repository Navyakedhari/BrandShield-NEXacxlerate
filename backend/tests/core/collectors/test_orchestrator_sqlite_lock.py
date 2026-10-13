"""A collector that writes through its own session must not hit 'database is locked' on file-based SQLite."""

from sqlalchemy import create_engine

from app.core.collectors.base import Candidate, CollectContext, Collector
from app.core.collectors.credits import CreditLedger
from app.core.collectors.orchestrator import run_collectors
from app.db.base import Base
from app.db.session import make_session_factory
from app.models import CollectorState

from .helpers import BRAND, NOW


class LedgerWritingCollector(Collector):
    name = "tavily"
    kind = "mixed"
    platforms = ("instagram",)

    def __init__(self, ledger: CreditLedger) -> None:
        super().__init__()
        self.ledger = ledger

    def is_configured(self) -> bool:
        return True

    def collect(self, brand, ctx: CollectContext) -> list[Candidate]:
        assert self.ledger.reserve(1)  # writes in a second session, like the real Tavily collector
        return []


def test_first_run_on_file_sqlite_does_not_lock(tmp_path):
    # a short busy timeout: without the fix the second session waits for the first one's lock and fails
    engine = create_engine(f"sqlite:///{tmp_path / 'lock.db'}", connect_args={"check_same_thread": False, "timeout": 0.3})
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    ledger = CreditLedger(sf, 100, now=lambda: NOW)
    result = run_collectors(sf, BRAND, [LedgerWritingCollector(ledger)], now=lambda: NOW)
    assert [r.status for r in result.reports] == ["online"], [r.message for r in result.reports]
    assert ledger.snapshot()["used"] == 1
    with sf() as db:
        assert db.query(CollectorState).filter_by(collector="tavily").one().status == "online"

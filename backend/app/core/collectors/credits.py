"""Persistent monthly credit counter for Tavily (table api_usage) with a hard budget guard.

Tavily Search API credit cost per call (documented): search_depth "basic" = 1 credit, "advanced" = 2.
`reserve()` is atomic: it adds the cost only if used + cost <= budget, in one UPDATE, so concurrent scans
cannot overshoot. `refund()` gives the cost back when the call provably did not run. The API-reported
usage (`usage.credits` in the response, when present) is stored in a second row (provider
"tavily:reported") and the guard uses the larger of the two counters.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config.time import utcnow
from app.models import ApiUsage

PROVIDER = "tavily"
REPORTED = "tavily:reported"
CREDITS_BY_DEPTH = {"basic": 1, "advanced": 2}  # fast/ultra-fast are 1 as well


def credit_cost(search_depth: str) -> int:
    return CREDITS_BY_DEPTH.get(search_depth, 1 if search_depth in ("fast", "ultra-fast") else 2)


def month_key(now: datetime) -> str:
    return f"{now.year:04d}-{now.month:02d}"


class CreditLedger:
    def __init__(self, session_factory: sessionmaker[Session], budget: int, *, now: Callable[[], datetime] = utcnow) -> None:
        self._sf = session_factory
        self.budget = max(0, int(budget))
        self._now = now

    # -- reads
    def _row_credits(self, db: Session, provider: str, month: str) -> int:
        return db.scalar(select(ApiUsage.credits).where(ApiUsage.provider == provider, ApiUsage.month == month)) or 0

    def snapshot(self) -> dict:
        month = month_key(self._now())
        with self._sf() as db:
            counted = self._row_credits(db, PROVIDER, month)
            reported = self._row_credits(db, REPORTED, month)
        used = max(counted, reported)
        return {
            "provider": "tavily",
            "month": month,
            "used": used,
            "budget": self.budget,
            "remaining": max(0, self.budget - used),
            "reported_used": reported,
        }

    def remaining(self) -> int:
        return self.snapshot()["remaining"]

    # -- writes
    def _ensure_row(self, db: Session, provider: str, month: str) -> None:
        if db.scalar(select(ApiUsage.id).where(ApiUsage.provider == provider, ApiUsage.month == month)) is None:
            try:
                db.add(ApiUsage(provider=provider, month=month, credits=0))
                db.commit()
            except IntegrityError:  # another worker created it first
                db.rollback()

    def reserve(self, cost: int) -> bool:
        """Atomically add `cost` if it fits in the budget. False means: do not call the API."""
        month = month_key(self._now())
        with self._sf() as db:
            self._ensure_row(db, PROVIDER, month)
            # Tavily's own report can run ahead of our counter (shared key, refunds): it must fit as well
            if self._row_credits(db, REPORTED, month) + cost > self.budget:
                return False
            res = db.execute(
                update(ApiUsage)
                .where(ApiUsage.provider == PROVIDER, ApiUsage.month == month, ApiUsage.credits + cost <= self.budget)
                .values(credits=ApiUsage.credits + cost, updated_at=utcnow())
            )
            db.commit()
            return res.rowcount == 1

    def refund(self, cost: int) -> None:
        month = month_key(self._now())
        with self._sf() as db:
            db.execute(
                update(ApiUsage)
                .where(ApiUsage.provider == PROVIDER, ApiUsage.month == month, ApiUsage.credits >= cost)
                .values(credits=ApiUsage.credits - cost, updated_at=utcnow())
            )
            db.commit()

    def record_reported(self, credits: int) -> None:
        """Store the credits the API says it charged for one call (accumulated per month)."""
        if credits <= 0:
            return
        month = month_key(self._now())
        with self._sf() as db:
            self._ensure_row(db, REPORTED, month)
            db.execute(
                update(ApiUsage)
                .where(ApiUsage.provider == REPORTED, ApiUsage.month == month)
                .values(credits=ApiUsage.credits + credits, updated_at=utcnow())
            )
            db.commit()

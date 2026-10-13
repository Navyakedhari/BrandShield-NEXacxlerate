"""Scan scheduler: a pure decision function plus a simple in-process loop.

Credit-aware rule (documented in docs/DECISIONS.md), applied only when Tavily is part of the live chain:
  cost            = tavily_max_queries_per_scan x credits per query (basic = 1)   -> credits one scan can use
  reserve         = one scan's worth of credits, never spent by the scheduler (kept for a manual scan)
  usable          = remaining - reserve
  usable < cost   -> scheduled scans are skipped until the month resets (manual scans still work)
  affordable      = usable // cost scans for the rest of the month
  min_interval    = days_left_in_month x 24h / affordable
  effective       = max(configured interval, min_interval)      -> the interval is stretched, never shortened
So an hourly schedule on a 900-credit budget quietly becomes "about every N hours" instead of burning the month
in a few days. Sample / SearXNG-only / iTunes-only setups have no credit limit and use the configured interval.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config.time import as_utc, utcnow
from app.core.collectors.credits import credit_cost
from app.core.collectors.selection import select_collectors
from app.core.pipeline.scan_pipeline import NoBrand, ScanAlreadyRunning, ScanContext, request_scan, running_scan
from app.models import Brand, Scan
from app.services import app_settings, data_mode

log = logging.getLogger("app.scheduler")


@dataclass(frozen=True)
class ScheduleDecision:
    run: bool
    reason: str
    next_run_at: datetime | None
    effective_interval_minutes: int
    configured_interval_minutes: int
    stretched: bool = False
    skipped_for_credits: bool = False


def next_month_start(now: datetime) -> datetime:
    now = as_utc(now)
    return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=UTC)


def decide_schedule(
    *,
    now: datetime,
    interval_minutes: int,
    last_scan_at: datetime | None,
    running: bool = False,
    credits_remaining: int | None = None,
    cost_per_scan: int | None = None,
) -> ScheduleDecision:
    """Should a scheduled scan start now, and when is the next one? Pure: no I/O, no clock.

    `credits_remaining` / `cost_per_scan` are None when no credit-limited source is in use.
    """
    now = as_utc(now)
    if interval_minutes <= 0:
        return ScheduleDecision(False, "Scheduled scans are off.", None, 0, 0)
    effective, stretched = interval_minutes, False
    if credits_remaining is not None and cost_per_scan and cost_per_scan > 0:
        usable = credits_remaining - cost_per_scan  # keep one scan's worth back for a manual scan
        if usable < cost_per_scan:
            return ScheduleDecision(
                False,
                f"Tavily credits are low ({credits_remaining} left, a scan uses about {cost_per_scan}). "
                "Scheduled scans are paused until the month resets; manual scans still work.",
                next_month_start(now), interval_minutes, interval_minutes, False, True,
            )
        affordable = usable // cost_per_scan
        days_left = max(1.0, (next_month_start(now) - now).total_seconds() / 86400)
        min_interval = math.ceil(days_left * 1440 / affordable)
        if min_interval > interval_minutes:
            effective, stretched = min_interval, True
    due = as_utc(last_scan_at) + timedelta(minutes=effective) if last_scan_at else now
    note = f" (stretched from {interval_minutes} to {effective} minutes to spread Tavily credits over the month)" if stretched else ""
    if running:
        return ScheduleDecision(False, "A scan is already running.", max(due, now), effective, interval_minutes, stretched)
    if now >= due:
        return ScheduleDecision(True, "A scheduled scan is due" + note + ".", now, effective, interval_minutes, stretched)
    return ScheduleDecision(False, "Next scheduled scan is not due yet" + note + ".", due, effective, interval_minutes, stretched)


WAITING_FOR_ASSETS = (
    "Waiting for the first official account or app. Scheduled scans start once the brand has at least one, so no credits are spent "
    "on a profile that cannot yet tell the brand's own pages from look-alikes."
)


def schedule_inputs(ctx: ScanContext, db) -> dict:
    """Read everything `decide_schedule` needs from settings, scans and the credit ledger."""
    mon = app_settings.get_monitoring(db)
    minutes, source = app_settings.interval_minutes(ctx.settings.scan_interval_minutes, mon)
    brand = db.scalars(select(Brand).where(Brand.archived_at.is_(None)).order_by(Brand.id)).first()
    last = running = None
    if brand is not None:
        last = db.scalar(select(Scan.created_at).where(Scan.brand_id == brand.id).order_by(Scan.id.desc()).limit(1))
        running = running_scan(db, brand.id)
    selection = select_collectors(ctx.settings, data_mode.get_data_mode(db))
    tav = selection.entry("tavily")
    credits = cost = None
    if selection.effective_mode == "live" and tav is not None and tav.active and tav.configured:
        credits = ctx.runtime.ledger.snapshot()["remaining"]
        cost = ctx.settings.tavily_max_queries_per_scan * credit_cost(ctx.settings.tavily_search_depth)
    return {
        "interval_minutes": minutes, "interval_source": source, "schedule": mon["schedule"], "last_scan_at": last,
        "running": running is not None, "credits_remaining": credits, "cost_per_scan": cost, "brand": brand,
        "official_assets": (len(brand.accounts) + len(brand.apps)) if brand is not None else 0,
    }


def schedule_status(ctx: ScanContext, db, now: datetime | None = None) -> dict:
    """Public description of the schedule (dashboard, GET /scan/status, Settings)."""
    now = now or ctx.now()
    inp = schedule_inputs(ctx, db)
    enabled = ctx.settings.scheduler_enabled and inp["interval_minutes"] > 0 and inp["brand"] is not None
    waiting = inp["brand"] is not None and inp["official_assets"] == 0 and inp["interval_minutes"] > 0
    d = decide_schedule(now=now, interval_minutes=inp["interval_minutes"], last_scan_at=inp["last_scan_at"], running=inp["running"],
                        credits_remaining=inp["credits_remaining"], cost_per_scan=inp["cost_per_scan"])
    return {
        "enabled": enabled,
        "schedule": "off" if inp["interval_minutes"] == 0 else inp["schedule"],
        "interval_minutes": inp["interval_minutes"],
        "interval_source": inp["interval_source"],
        "effective_interval_minutes": d.effective_interval_minutes,
        "stretched_for_credits": d.stretched,
        "paused_for_credits": d.skipped_for_credits,
        "waiting_for_official_assets": waiting,
        "next_scan_at": None if waiting else (d.next_run_at if enabled else None),
        "reason": WAITING_FOR_ASSETS if waiting and ctx.settings.scheduler_enabled else (
            d.reason if enabled else ("The scheduler is disabled." if not ctx.settings.scheduler_enabled else d.reason)),
    }


def scheduler_tick(ctx: ScanContext, now: datetime | None = None) -> ScheduleDecision:
    """One pass of the loop: decide, and start a scan (started_by 'scheduler') when due. Never raises."""
    now = now or utcnow()
    try:
        with ctx.session_factory() as db:
            inp = schedule_inputs(ctx, db)
            if inp["brand"] is None:
                return ScheduleDecision(False, "No brand profile yet.", None, 0, inp["interval_minutes"])
            if inp["official_assets"] == 0 and inp["interval_minutes"] > 0:
                return ScheduleDecision(False, WAITING_FOR_ASSETS, None, inp["interval_minutes"], inp["interval_minutes"])
            d = decide_schedule(now=now, interval_minutes=inp["interval_minutes"], last_scan_at=inp["last_scan_at"], running=inp["running"],
                                credits_remaining=inp["credits_remaining"], cost_per_scan=inp["cost_per_scan"])
            if d.run:
                try:
                    request_scan(ctx, db, brand_id=inp["brand"].id, scope="all", user=None)
                except (ScanAlreadyRunning, NoBrand):
                    pass
            return d
    except Exception:  # noqa: BLE001 - the loop must survive anything
        log.exception("scheduler tick failed")
        return ScheduleDecision(False, "Scheduler error (see log).", None, 0, 0)


class SchedulerLoop:
    """Daemon thread that calls `scheduler_tick` every `poll_seconds`. Started from the app lifespan only when enabled."""

    def __init__(self, ctx: ScanContext, poll_seconds: int) -> None:
        self.ctx = ctx
        self.poll = max(5, poll_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(target=self._loop, name="scan-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.poll):
            scheduler_tick(self.ctx)

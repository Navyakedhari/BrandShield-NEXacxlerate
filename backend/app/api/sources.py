import math
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.deps import get_current_user, require_admin, require_writer
from app.config.time import as_utc, iso_z, utcnow
from app.core.collectors.credits import credit_cost
from app.core.collectors.google_play import GooglePlayCollector
from app.core.collectors.itunes import ITunesCollector
from app.core.collectors.orchestrator import (
    BACKOFF_SCHEDULE_SECONDS,
    collector_states,
    ensure_sources,
    load_brand_query,
    run_collectors,
)
from app.core.collectors.sample import SampleCollector
from app.core.collectors.searxng import SearxngCollector
from app.core.collectors.selection import CollectorRuntime, Selection, select_collectors
from app.core.collectors.tavily import TavilyCollector
from app.db.session import get_db
from app.models import Source, User
from app.services import audit, data_mode
from app.services.scheduler import schedule_status

router = APIRouter()

_PLATFORMS_BY_COLLECTOR = {
    c.name: c.platforms for c in (TavilyCollector, SearxngCollector, ITunesCollector, GooglePlayCollector, SampleCollector)
}
RETRY_POLICY = ",".join(
    f"{s // 3600}h" if s >= 3600 else f"{s // 60}m" for s in BACKOFF_SCHEDULE_SECONDS
)


def retry_estimate(settings, selection: Selection, runtime: CollectorRuntime | None, platform: str) -> dict:
    """What a manual retry of this source could cost. Only Tavily spends credits (one per query); iTunes, SearXNG and the sample
    collector are free. The figure is the per-scan query cap times the credit cost of the configured search depth: an upper bound."""
    entry = selection.entry("tavily")
    if entry is None or not entry.active or not entry.configured or platform not in TavilyCollector.platforms:
        return {"credits": 0, "provider": None, "remaining": None, "note": "This retry does not use paid credits."}
    cost = settings.tavily_max_queries_per_scan * credit_cost(settings.tavily_search_depth)
    remaining = runtime.ledger.snapshot()["remaining"] if runtime is not None else None
    return {"credits": cost, "provider": "tavily", "remaining": remaining,
            "note": f"Up to {cost} Tavily credits (about {settings.tavily_max_queries_per_scan} searches). Credits are spent even if nothing new is found."}


def get_runtime(request: Request) -> CollectorRuntime:
    return request.app.state.collector_runtime


def current_selection(db: Session, request: Request) -> Selection:
    return select_collectors(request.app.state.settings, data_mode.get_data_mode(db))


class DataModeBody(BaseModel):
    data_mode: Literal["live", "sample", "auto"]


class MonitoredBody(BaseModel):
    monitored: bool


def _iso(dt: datetime | None) -> str | None:
    return iso_z(dt) if dt else None


def source_item(row: Source, selection: Selection, states: dict, now: datetime, settings=None, runtime: CollectorRuntime | None = None) -> dict:
    covering = [n for n in selection.names if row.platform in _PLATFORMS_BY_COLLECTOR.get(n, ())]
    sts = [states[n] for n in covering if n in states]
    cached_ats = [as_utc(s.cached_at) for s in sts if s.cached_at and s.cache]
    cached_at = max(cached_ats) if cached_ats else None
    retries = [as_utc(s.next_retry_at) for s in sts if s.next_retry_at and s.status == "unavailable"]
    next_retry = min(retries) if retries else None

    status, message, detail, stale_since = row.status, row.message, row.detail, row.stale_since
    mode_matches = row.source == selection.effective_mode
    # data written under the other data mode (sample vs live) must not be presented as current
    attempted = any(s.last_attempt_at for s in sts if s.source == selection.effective_mode)
    never_synced = (row.last_sync is None or not mode_matches) and not attempted
    if never_synced:
        if selection.effective_mode == "sample":
            status, message = "online", "Not scanned yet. Sample data will be used."
        else:
            entries = [selection.entry(n) for n in covering]
            if any(e and e.configured for e in entries):
                status, message = "online", "Not checked yet."
            else:
                status = "unavailable"
                why = next((e.reason for e in entries if e), "No data source covers this platform.")
                message = f"No live data source is configured. {why}"
    retry_in = None
    if status == "unavailable" and next_retry:
        retry_in = max(1, math.ceil((next_retry - now).total_seconds() / 60))
    return {
        "id": row.id,
        "slug": row.platform,
        "name": row.name,
        "kind": row.kind,
        "description": row.description,
        "status": status,
        "monitored": row.monitored,
        "message": message,
        "last_sync_at": _iso(row.last_sync) if not never_synced else None,
        "detail": detail,
        "retry_in_minutes": retry_in,
        "next_retry_at": _iso(next_retry),
        "stale_since": _iso(stale_since),
        "cached_at": _iso(cached_at),
        "cached_age_seconds": max(0, int((now - cached_at).total_seconds())) if cached_at else None,
        "collectors": covering,
        "source": row.source if not never_synced else selection.effective_mode,
        "retry_estimate": retry_estimate(settings, selection, runtime, row.platform) if settings is not None else None,
    }


@router.get("/sources")
def list_sources(
    request: Request,
    kind: Literal["social", "store"] | None = Query(None),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: CollectorRuntime = Depends(get_runtime),
) -> dict:
    rows = ensure_sources(db)
    selection = current_selection(db, request)
    states = collector_states(db)
    now = utcnow()
    items = [source_item(r, selection, states, now, request.app.state.settings, runtime) for r in rows if kind is None or r.kind == kind]
    syncs = [as_utc(r.last_sync) for r in rows if r.last_sync]
    sched = schedule_status(request.app.state.scan_ctx, db, now)
    return {
        "data_mode": selection.effective_mode,
        "requested_data_mode": selection.requested_mode,
        "summary": {
            "online": sum(i["status"] == "online" for i in items),
            "slow": sum(i["status"] == "slow" for i in items),
            "unavailable": sum(i["status"] == "unavailable" for i in items),
            "total": len(items),
            "next_scan_at": _iso(sched["next_scan_at"]),
            "schedule": sched["schedule"],
            "last_full_sync_at": _iso(max(syncs)) if syncs else None,
            "retry_policy": RETRY_POLICY,
        },
        "items": items,
        "tavily_credits": runtime.ledger.snapshot(),
        # URL reputation provider (Google Safe Browsing): never contains the key, only whether one is set
        "reputation": runtime.safe_browsing.status(),
    }


@router.patch("/sources/{source_id}")
def set_monitored(
    source_id: int,
    body: MonitoredBody,
    request: Request,
    user: User = Depends(require_writer),
    db: Session = Depends(get_db),
) -> dict:
    ensure_sources(db)
    row = db.get(Source, source_id)
    if row is None:
        raise HTTPException(404, "Source not found")
    if row.monitored != body.monitored:
        row.monitored = body.monitored
        audit.record(db, user, "source.monitor_toggled", row.platform, f"monitored={str(body.monitored).lower()}")
        db.commit()
    return source_item(row, current_selection(db, request), collector_states(db), utcnow(), request.app.state.settings, request.app.state.collector_runtime)


@router.post("/sources/{source_id}/retry", status_code=202)
def retry_source(
    source_id: int,
    request: Request,
    confirm: bool = Query(False, description="Required when the retry can spend paid credits (see `retry_estimate` on the source)."),
    user: User = Depends(require_writer),
    db: Session = Depends(get_db),
    runtime: CollectorRuntime = Depends(get_runtime),
) -> dict:
    """Runs the collectors for this platform now, ignoring back-off. Synchronous until Phase 4 adds jobs."""
    ensure_sources(db)
    row = db.get(Source, source_id)
    if row is None:
        raise HTTPException(404, "Source not found")
    if not row.monitored:
        raise HTTPException(409, "This source is not monitored. Turn monitoring on first.")
    selection = current_selection(db, request)
    item = source_item(row, selection, collector_states(db), utcnow())
    if item["status"] == "online" and row.last_sync is not None:
        raise HTTPException(409, "This source is online. Nothing to retry.")
    brand = load_brand_query(db)
    if brand is None:
        raise HTTPException(409, "Create the brand profile before retrying a source.")
    collectors = [c for c in runtime.build(selection) if row.platform in c.platforms]
    if not collectors:
        raise HTTPException(409, "No data source is active for this platform in the current data mode.")
    settings = request.app.state.settings
    estimate = retry_estimate(settings, selection, runtime, row.platform)
    if estimate["credits"] > 0 and not confirm:
        return JSONResponse(status_code=409, content={
            "detail": f"Retrying {row.name} can spend up to {estimate['credits']} {estimate['provider'].title()} credits, even if nothing new is found. "
                      "Confirm to continue.",
            "code": "confirmation_required", "estimated_credits": estimate["credits"], "provider": estimate["provider"],
            "credits_remaining": estimate["remaining"],
        })
    result = run_collectors(
        request.app.state.session_factory,
        brand,
        collectors,
        only_platforms=frozenset({row.platform}),
        force=True,
        slow_threshold_ms=settings.source_slow_threshold_ms,
        now=runtime.now,
    )
    audit.record(db, user, "source.retry", row.platform, ", ".join(f"{r.collector}={r.status}" for r in result.reports)
                 + (f"; up to {estimate['credits']} credits, confirmed" if estimate["credits"] else ""))
    db.commit()
    db.refresh(row)
    new = source_item(row, selection, collector_states(db), utcnow(), settings, runtime)
    return {"job_id": f"retry_{uuid.uuid4().hex[:8]}", "source_id": row.id, "state": "done", "status": new["status"], "source": new,
            "reports": [r.as_dict() for r in result.reports], "estimated_credits": estimate["credits"]}


@router.get("/settings/data-mode")
def get_data_mode(request: Request, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    sel = current_selection(db, request)
    return sel.as_dict()


@router.put("/settings/data-mode")
def put_data_mode(
    body: DataModeBody,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    data_mode.set_data_mode(db, body.data_mode, user)
    return current_selection(db, request).as_dict()


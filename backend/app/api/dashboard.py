from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_ctx, mode_of, resolve_brand
from app.api.sources import current_selection, source_item
from app.config.deps import get_current_user
from app.config.time import iso_z
from app.core.collectors.orchestrator import collector_states, ensure_sources
from app.core.pipeline.scan_pipeline import ScanContext, scan_view
from app.db.session import get_db
from app.models import Scan, User
from app.services import dashboard
from app.services.scheduler import schedule_status

router = APIRouter()


@router.get("/dashboard")
def get_dashboard(
    request: Request,
    brand_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ctx: ScanContext = Depends(get_ctx),
) -> dict:
    """Every number is computed from stored rows for the current data mode. Nothing is hard-coded."""
    brand = resolve_brand(db, brand_id)
    mode = mode_of(db, request)
    now = ctx.now()
    out = dashboard.build(db, brand, mode, now)

    rows = ensure_sources(db)
    selection = current_selection(db, request)
    states = collector_states(db)
    items = [source_item(r, selection, states, now) for r in rows]
    out["sources"] = [{"id": i["id"], "slug": i["slug"], "name": i["name"], "kind": i["kind"], "status": i["status"], "monitored": i["monitored"],
                       "message": i["message"]} for i in items]
    out["sources_online"] = sum(i["status"] == "online" for i in items)
    out["sources_total"] = len(items)

    last = db.scalars(select(Scan).where(Scan.brand_id == brand.id, Scan.state.in_(("completed", "partial", "failed"))).order_by(Scan.id.desc())).first()
    out["last_scan_at"] = iso_z(last.finished_at) if last and last.finished_at else None
    out["last_scan"] = scan_view(last) if last else None
    sched = schedule_status(ctx, db, now)
    out["next_scan_at"] = iso_z(sched["next_scan_at"]) if sched["next_scan_at"] else None
    out["schedule"] = {**sched, "next_scan_at": out["next_scan_at"]}
    out["credits"] = ctx.runtime.ledger.snapshot() if mode == "live" else None
    return out

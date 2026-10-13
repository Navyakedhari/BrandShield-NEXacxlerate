from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import get_ctx, mode_of, resolve_brand
from app.config.deps import get_current_user, require_writer
from app.config.time import iso_z
from app.core.pipeline.scan_pipeline import NoBrand, ScanAlreadyRunning, ScanContext, request_scan, running_scan, scan_view
from app.db.session import get_db
from app.models import Scan, Source, User
from app.services.scheduler import schedule_status

router = APIRouter()


class ScanBody(BaseModel):
    scope: Literal["all", "social", "apps"] = "all"
    brand_id: int | None = None


@router.post("/scan", status_code=202)
def start_scan(
    body: ScanBody | None = None,
    user: User = Depends(require_writer),
    db: Session = Depends(get_db),
    ctx: ScanContext = Depends(get_ctx),
):
    """Queue a scan. One scan per brand at a time: a second request gets 409 with the running scan."""
    body = body or ScanBody()
    try:
        scan = request_scan(ctx, db, brand_id=body.brand_id, scope=body.scope, user=user)
    except NoBrand as exc:
        raise HTTPException(409, "Create the brand profile before scanning.") from exc
    except ScanAlreadyRunning as exc:
        return JSONResponse(
            {"detail": "A scan is already running", "job_id": exc.scan.job_id, "scan_id": exc.scan.id, "scan": scan_view(exc.scan)}, status_code=409
        )
    db.refresh(scan)  # an inline run has already finished by now
    return {"job_id": scan.job_id, "scan_id": scan.id, "status": scan.state, "scan": scan_view(scan)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    scan = db.scalar(select(Scan).where(Scan.job_id == job_id))
    if scan is None:
        raise HTTPException(404, "Job not found")
    return scan_view(scan)


@router.get("/scan/status")
def scan_status(
    request: Request,
    brand_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ctx: ScanContext = Depends(get_ctx),
) -> dict:
    brand = resolve_brand(db, brand_id)
    last = db.scalars(select(Scan).where(Scan.brand_id == brand.id, Scan.state.in_(("completed", "partial"))).order_by(Scan.id.desc())).first()
    running = running_scan(db, brand.id)
    sync = db.scalar(select(func.max(Source.last_sync)))
    sched = schedule_status(ctx, db)
    mode = mode_of(db, request)
    return {
        "last_scan_at": iso_z(last.finished_at) if last and last.finished_at else None,
        "last_scan_status": last.state if last else None,
        "last_full_sync_at": iso_z(sync) if sync else None,
        "next_scan_at": iso_z(sched["next_scan_at"]) if sched["next_scan_at"] else None,
        "schedule": sched["schedule"],
        "running_job_id": running.job_id if running else None,
        "data_mode": mode,
        "is_sample": mode == "sample",
        "scheduler": {**sched, "next_scan_at": iso_z(sched["next_scan_at"]) if sched["next_scan_at"] else None},
    }


@router.get("/scans")
def list_scans(
    brand_id: int | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    brand = resolve_brand(db, brand_id)
    q = select(Scan).where(Scan.brand_id == brand.id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Scan.id.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [scan_view(s) for s in rows]}


@router.get("/scans/latest")
def latest_scan(brand_id: int | None = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    brand = resolve_brand(db, brand_id)
    scan = db.scalars(select(Scan).where(Scan.brand_id == brand.id).order_by(Scan.id.desc())).first()
    return {"scan": scan_view(scan) if scan else None}


@router.get("/scans/{scan_id}")
def get_scan(scan_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found")
    return scan_view(scan)

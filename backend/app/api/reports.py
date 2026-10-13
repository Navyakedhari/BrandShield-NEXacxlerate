from datetime import UTC, date, datetime, time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import mode_of, resolve_brand
from app.config.deps import get_current_user, require_writer
from app.config.time import iso_z, utcnow
from app.db.session import get_db
from app.models import Brand, Report, User
from app.services import audit
from app.services import reports as svc
from app.services.detections import ActionError

router = APIRouter(prefix="/reports")
_HTML_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'", "X-Content-Type-Options": "nosniff"}


class ReportIn(BaseModel):
    detection_ids: list[int] | None = Field(default=None, max_length=1000)
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    types: list[str] | None = None
    status: str | None = None
    include_evidence: bool = True
    format: Literal["csv", "html", "pdf"] = "html"
    title: str | None = Field(default=None, max_length=200)
    brand_id: int | None = None

    model_config = {"populate_by_name": True}


def _wrap(fn):
    try:
        return fn()
    except ActionError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


def _bounds(from_: date | None, to: date | None) -> tuple[datetime | None, datetime | None]:
    return (datetime.combine(from_, time.min, UTC) if from_ else None, datetime.combine(to, time.max, UTC) if to else None)


def _period(from_: date | None, to: date | None) -> str:
    return f"{from_ or 'start'} to {to or 'today'}" if (from_ or to) else "all time"


def report_out(db: Session, r: Report) -> dict:
    creator = db.get(User, r.created_by) if r.created_by else None
    approver = db.get(User, r.approved_by) if r.approved_by else None
    p = r.params or {}
    return {
        "id": r.id, "title": r.title, "filename": r.filename, "format": r.format, "status": r.status, "size_bytes": r.size_bytes,
        "created_by": creator.name if creator else None, "created_at": iso_z(r.created_at), "approved_by": approver.name if approver else None,
        "approved_at": iso_z(r.approved_at) if r.approved_at else None, "from": p.get("from"), "to": p.get("to"), "count": len(r.snapshot or []),
        "detection_ids": r.detection_ids or [], "stats": p.get("stats", {}), "include_evidence": bool(p.get("include_evidence")),
        "includes_sample": r.includes_sample, "source": r.source, "download_url": f"/api/reports/{r.id}/download",
        "preview_url": f"/api/reports/{r.id}/preview", "csv_url": f"/api/reports/{r.id}/export.csv", "html_url": f"/api/reports/{r.id}/export.html",
    }


def _preview_rows(request: Request, db: Session, brand: Brand, from_, to, types, status, ids):
    mode = mode_of(db, request)
    f, t = _bounds(from_, to)
    dets = _wrap(lambda: svc.select_detections(db, brand, ids=ids, from_=f, to=t, types=types, status=status, mode=mode))
    rows = [svc.row_for(d, i + 1) for i, d in enumerate(dets)]
    return mode, dets, rows


def _preview_params(types: str | None, ids: str | None) -> tuple[list[str] | None, list[int] | None]:
    try:
        return ([t for t in types.split(",") if t] if types else None, [int(i) for i in ids.split(",") if i] if ids else None)
    except ValueError as exc:
        raise HTTPException(422, "ids must be a comma separated list of numbers") from exc


@router.get("/preview")
def preview(
    request: Request, from_: date | None = Query(None, alias="from"), to: date | None = None, types: str | None = None, status: str | None = None,
    ids: str | None = None, include_evidence: bool = True, brand_id: int | None = None, _: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    brand = resolve_brand(db, brand_id)
    tl, il = _preview_params(types, ids)
    mode, dets, rows = _preview_rows(request, db, brand, from_, to, tl, status, il)
    return {
        "title": "Brand Protection Report", "brand": brand.name, "from": str(from_) if from_ else None, "to": str(to) if to else None,
        "data_mode": mode, "is_sample": mode == "sample", "includes_sample": any(d.source == "sample" for d in dets),
        "stats": svc.stats(db, brand, dets), "include_evidence": include_evidence, "rows": [{k: r[k] for k in ("n", "id", "name", "platform", "type", "score", "status")} for r in rows],
    }


@router.get("/preview.html")
def preview_html(
    request: Request, from_: date | None = Query(None, alias="from"), to: date | None = None, types: str | None = None, status: str | None = None,
    ids: str | None = None, include_evidence: bool = True, brand_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> HTMLResponse:
    brand = resolve_brand(db, brand_id)
    tl, il = _preview_params(types, ids)
    _, dets, rows = _preview_rows(request, db, brand, from_, to, tl, status, il)
    body = svc.to_html(title="Brand Protection Report (preview)", brand_name=brand.name, status="draft", rows=rows, stats_=svc.stats(db, brand, dets),
                       include_evidence=include_evidence, created_by=user.name, created_at=utcnow(), approved_by=None, approved_at=None,
                       period=_period(from_, to), includes_sample=any(d.source == "sample" for d in dets))
    return HTMLResponse(body, headers=_HTML_HEADERS)


@router.post("", status_code=201)
def create_report(body: ReportIn, request: Request, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> dict:
    """Creates a DRAFT from the selected detections (or, by default, the confirmed ones). Nothing is sent anywhere."""
    if body.format == "pdf":
        raise HTTPException(422, "PDF export is not available (no PDF library is installed). Use html (printable, Print to PDF) or csv.")
    brand = resolve_brand(db, body.brand_id)
    mode, dets, rows = _preview_rows(request, db, brand, body.from_, body.to, body.types, body.status, body.detection_ids)
    if not rows:
        raise HTTPException(422, "No detections match. Select detections or confirm some first.")
    now = utcnow()
    includes_sample = any(d.source == "sample" for d in dets)
    st = svc.stats(db, brand, dets)
    report = Report(
        brand_id=brand.id, title=(body.title or "Brand Protection Report").strip() or "Brand Protection Report", filename=svc.filename_for(brand, body.format, now),
        format=body.format, size_bytes=0, status="draft", detection_ids=[d.id for d in dets], snapshot=rows,
        params={"from": str(body.from_) if body.from_ else None, "to": str(body.to) if body.to else None, "types": body.types, "status": body.status,
                "include_evidence": body.include_evidence, "stats": st, "period": _period(body.from_, body.to), "data_mode": mode},
        includes_sample=includes_sample, source="sample" if all(d.source == "sample" for d in dets) else "live", created_by=user.id, created_at=now,
    )
    db.add(report)
    db.flush()
    rendered = svc.report_csv(report) if body.format == "csv" else svc.report_html(db, report, brand)
    report.size_bytes = len(rendered.encode("utf-8"))
    audit.record(db, user, "report.generated", report.filename, f"draft with {len(rows)} detections ({body.format})", source=report.source)
    db.commit()
    return report_out(db, report)


@router.get("")
def list_reports(
    limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0), brand_id: int | None = None,
    _: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    brand = resolve_brand(db, brand_id)
    q = select(Report).where(Report.brand_id == brand.id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Report.id.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [report_out(db, r) for r in rows]}


def _get(db: Session, report_id: int) -> Report:
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Report not found")
    return r


@router.get("/{report_id}")
def get_report(report_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    r = _get(db, report_id)
    out = report_out(db, r)
    out["rows"] = [{k: row[k] for k in ("n", "id", "name", "platform", "type", "score", "status")} for row in (r.snapshot or [])]
    return out


@router.get("/{report_id}/preview")
def report_preview(report_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> HTMLResponse:
    r = _get(db, report_id)
    return HTMLResponse(svc.report_html(db, r, db.get(Brand, r.brand_id)), headers=_HTML_HEADERS)


def _attachment(content: str, media: str, filename: str, headers: dict | None = None) -> Response:
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff", **(headers or {})})


@router.get("/{report_id}/export.csv")
def export_csv(report_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    r = _get(db, report_id)
    return _attachment(svc.report_csv(r), "text/csv; charset=utf-8", r.filename.rsplit(".", 1)[0] + ".csv")


@router.get("/{report_id}/export.html")
def export_html(report_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    r = _get(db, report_id)
    return _attachment(svc.report_html(db, r, db.get(Brand, r.brand_id)), "text/html; charset=utf-8", r.filename.rsplit(".", 1)[0] + ".html", _HTML_HEADERS)


@router.get("/{report_id}/download")
def download(report_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    r = _get(db, report_id)
    if r.format == "csv":
        return _attachment(svc.report_csv(r), "text/csv; charset=utf-8", r.filename)
    return _attachment(svc.report_html(db, r, db.get(Brand, r.brand_id)), "text/html; charset=utf-8", r.filename, _HTML_HEADERS)


@router.post("/{report_id}/approve")
def approve(report_id: int, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> dict:
    """A person approves the draft. This only records the approval: nothing is sent anywhere."""
    r = _get(db, report_id)
    if r.status == "approved":
        raise HTTPException(409, "This report is already approved")
    r.status, r.approved_by, r.approved_at = "approved", user.id, utcnow()
    audit.record(db, user, "report.approved", r.filename, f"{len(r.snapshot or [])} detections", source=r.source)
    db.commit()
    return report_out(db, r)

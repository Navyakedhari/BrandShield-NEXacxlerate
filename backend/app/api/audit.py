import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.deps import require_writer
from app.config.time import UtcDatetime
from app.db.session import get_db
from app.models import AuditLog, User

router = APIRouter()


class AuditItem(BaseModel):
    id: int
    ts: UtcDatetime
    actor: str
    actor_id: int | None
    action: str
    target: str
    detail: str
    source: str


class AuditList(BaseModel):
    total: int
    items: list[AuditItem]


def _filtered(from_: datetime | None, to: datetime | None, actor: str | None, action: str | None):
    q = select(AuditLog)
    if from_:
        q = q.where(AuditLog.ts >= from_)
    if to:
        q = q.where(AuditLog.ts <= to)
    if actor:
        q = q.where(AuditLog.actor == actor)
    if action:
        q = q.where(AuditLog.action == action)
    return q


@router.get("/audit", response_model=AuditList)
def list_audit(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    actor: str | None = None,
    action: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_writer),  # admin and analyst; viewers cannot read the audit log
    db: Session = Depends(get_db),
) -> AuditList:
    q = _filtered(from_, to, actor, action)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()
    return AuditList(total=total, items=[AuditItem.model_validate(r, from_attributes=True) for r in rows])


def _safe_cell(value: str) -> str:
    # Neutralise spreadsheet formula injection.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@router.get("/audit/export.csv")
def export_audit(_: User = Depends(require_writer), db: Session = Depends(get_db)) -> Response:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ts", "actor", "action", "target", "detail"])
    for r in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        w.writerow([r.ts.strftime("%Y-%m-%dT%H:%M:%SZ"), _safe_cell(r.actor), r.action, _safe_cell(r.target), _safe_cell(r.detail)])
    return Response(
        out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'}
    )

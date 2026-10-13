from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import mode_of, resolve_brand
from app.config.config import Settings
from app.config.deps import get_current_user, get_settings_dep, require_writer
from app.core.pipeline import evidence
from app.db.session import get_db
from app.models import Detection, User
from app.services import detections as svc

router = APIRouter(prefix="/detections")


class StatusBody(BaseModel):
    status: str
    note: str = Field(default="", max_length=1000)


class ResolutionBody(BaseModel):
    resolution: Literal["true_positive", "false_positive", "resolved", "monitoring"]
    note: str = Field(default="", max_length=1000)


class NoteBody(BaseModel):
    note: str = Field(default="", max_length=1000)


class MarkOfficialBody(BaseModel):
    note: str = Field(default="", max_length=1000)
    publisher: str | None = Field(default=None, max_length=120)


def _get(db: Session, detection_id: int) -> Detection:
    d = db.get(Detection, detection_id)
    if d is None:
        raise HTTPException(404, "Detection not found")
    return d


def _wrap(fn):
    try:
        return fn()
    except svc.ActionError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


def _source_filter(source: str | None, mode: str) -> str | None:
    """Default: only records that belong to the current data mode. `source=all` shows both."""
    if source is None:
        return mode
    return None if source == "all" else source


@router.get("")
def list_detections(
    request: Request,
    category: Literal["social", "apps", "web"] | None = None,
    type: str | None = None,
    platform: str | None = None,
    band: Literal["high", "medium", "low"] | None = None,
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None,
    resolution: Literal["true_positive", "false_positive", "resolved", "monitoring"] | None = None,
    status: list[str] | None = Query(None),
    source: Literal["live", "sample", "all"] | None = None,
    q: str | None = Query(None, max_length=200),
    min_score: int | None = Query(None, ge=0, le=100),
    sort: Literal["score_desc", "score_asc", "found_desc", "found_asc", "last_seen_desc"] = "score_desc",
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    brand_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    brand = resolve_brand(db, brand_id)
    mode = mode_of(db, request)
    dtype, cat = _wrap(lambda: svc.normalize_type(type))
    statuses = None
    if status:
        statuses = [svc.STATUS_ALIASES.get(s, s) for s in status]
        bad = [s for s in statuses if s not in ("new", "reviewing", "confirmed", "dismissed", "marked_official")]
        if bad:
            raise HTTPException(422, "status must be one of: new, reviewing, confirmed, dismissed")
    f = svc.Filters(brand.id, category=category or cat, type=dtype, platform=platform, band=band, severity=severity, resolution=resolution, statuses=statuses,
                    source=_source_filter(source, mode), q=q, min_score=min_score, from_=from_, to=to)
    res = svc.list_detections(db, f, sort, limit, offset)
    items = [svc.summary_row(d) for d in res["rows"]]
    return {
        "total": res["total"], "limit": limit, "offset": offset, "data_mode": mode, "is_sample": mode == "sample",
        "includes_sample": any(i["source"] == "sample" for i in items), "counts": res["counts"], "items": items,
    }


@router.get("/excluded")
def excluded(
    request: Request,
    category: Literal["social", "apps", "web"] | None = None,
    brand_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    brand = resolve_brand(db, brand_id)
    mode = mode_of(db, request)
    items = svc.excluded_items(db, brand, category, None)
    return {"total": len(items), "data_mode": mode, "is_sample": mode == "sample", "items": items}


@router.get("/false-positives")
def false_positives(
    request: Request,
    category: Literal["social", "apps", "web"] | None = None,
    source: Literal["live", "sample", "all"] | None = None,
    brand_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    brand = resolve_brand(db, brand_id)
    mode = mode_of(db, request)
    items = svc.false_positive_items(db, brand, category, _source_filter(source, mode))
    return {"total": len(items), "data_mode": mode, "is_sample": mode == "sample", "items": items}


@router.get("/{detection_id}")
def get_detection(detection_id: int, request: Request, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    d = _get(db, detection_id)
    brand = resolve_brand(db, d.brand_id)
    mode = mode_of(db, request)
    out = svc.detail(db, d, brand)
    out.update({"data_mode": mode, "is_sample": mode == "sample"})
    return out


@router.get("/{detection_id}/evidence")
def get_evidence(
    detection_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> dict:
    return svc.evidence_bundle(_get(db, detection_id), settings.data_dir)


@router.get("/{detection_id}/evidence/screenshot")
def get_screenshot(
    detection_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> FileResponse:
    d = _get(db, detection_id)
    path = evidence.safe_path(settings.data_dir, (d.evidence or {}).get("screenshot_path"))
    if path is None:
        raise HTTPException(404, "No screenshot was captured (screenshots are off by default)")
    return FileResponse(path, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"})


def _status_response(db: Session, d: Detection, audit_id: int | None) -> dict:
    out = svc.detail(db, d, resolve_brand(db, d.brand_id))
    out["audit_id"] = audit_id
    return out


@router.post("/{detection_id}/status")
def set_status(detection_id: int, body: StatusBody, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> dict:
    d = _get(db, detection_id)
    d, audit_id = _wrap(lambda: svc.change_status(db, d, user, body.status, body.note))
    return _status_response(db, d, audit_id)


@router.post("/{detection_id}/resolution")
def set_resolution(detection_id: int, body: ResolutionBody, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> dict:
    d = _get(db, detection_id)
    d, audit_id = _wrap(lambda: svc.set_resolution(db, d, user, body.resolution, body.note))
    return _status_response(db, d, audit_id)


def _shortcut(to: str):
    def endpoint(detection_id: int, body: NoteBody | None = None, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> dict:
        d = _get(db, detection_id)
        d, audit_id = _wrap(lambda: svc.change_status(db, d, user, to, (body.note if body else "")))
        return _status_response(db, d, audit_id)

    return endpoint


router.add_api_route("/{detection_id}/confirm", _shortcut("confirmed"), methods=["POST"])
router.add_api_route("/{detection_id}/keep-reviewing", _shortcut("reviewing"), methods=["POST"])
router.add_api_route("/{detection_id}/dismiss", _shortcut("dismissed"), methods=["POST"])


@router.post("/{detection_id}/mark-official")
def mark_official(
    detection_id: int, body: MarkOfficialBody | None = None, user: User = Depends(require_writer), db: Session = Depends(get_db)
) -> dict:
    body = body or MarkOfficialBody()
    d = _get(db, detection_id)
    brand = resolve_brand(db, d.brand_id)
    res = _wrap(lambda: svc.mark_official(db, brand, d, user, body.note, body.publisher))
    return {"detection": svc.detail(db, d, brand), "allowlist_entry": res["allowlist_entry"], "official_assets": res["official_assets"],
            "audit_id": res["audit_id"]}

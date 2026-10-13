"""Manual candidate entry, CSV / JSON import and candidate image upload. Writers only (analyst, admin). No URL is ever opened."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import get_ctx, resolve_brand
from app.config.config import Settings
from app.config.deps import get_current_user, get_settings_dep, require_writer
from app.core.pipeline import candidate_intake as ci
from app.core.pipeline.candidate_images import process_candidate_image, save_candidate_image
from app.core.pipeline.logo import LogoError
from app.core.pipeline.scan_pipeline import ScanContext
from app.db.session import get_db
from app.models import CandidateImage, User
from app.services import audit

router = APIRouter(prefix="/candidates")
images = APIRouter(prefix="/candidate-images")


def _single(kind: str, body, brand_id: int | None, user: User, db: Session, ctx: ScanContext) -> dict:
    brand = resolve_brand(db, brand_id)
    report = ci.run_rows(ctx, db, brand, user, kind, [body.model_dump(mode="json")], "manual", dry_run=False)
    r = report["results"][0]
    if r["status"] == "error":
        raise HTTPException(422, r["message"])
    return r


@router.post("/social", status_code=201)
def add_social(body: ci.SocialIn, brand_id: int | None = None, user: User = Depends(require_writer), db: Session = Depends(get_db), ctx: ScanContext = Depends(get_ctx)) -> dict:
    return _single("social", body, brand_id, user, db, ctx)


@router.post("/app", status_code=201)
def add_app(body: ci.AppIn, brand_id: int | None = None, user: User = Depends(require_writer), db: Session = Depends(get_db), ctx: ScanContext = Depends(get_ctx)) -> dict:
    return _single("app", body, brand_id, user, db, ctx)


@router.post("/web", status_code=201)
def add_web(body: ci.WebIn, brand_id: int | None = None, user: User = Depends(require_writer), db: Session = Depends(get_db), ctx: ScanContext = Depends(get_ctx)) -> dict:
    return _single("web", body, brand_id, user, db, ctx)


@router.post("/import")
def import_candidates(
    kind: Literal["social", "app", "web"],
    format: Literal["csv", "json"],
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    brand_id: int | None = None,
    user: User = Depends(require_writer),
    db: Session = Depends(get_db),
    ctx: ScanContext = Depends(get_ctx),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Strict-schema import with per-row errors and a partial-success report. Valid rows are saved even when others fail."""
    brand = resolve_brand(db, brand_id)
    data = file.file.read(settings.import_max_bytes + 1)  # never read more than the cap + 1 byte
    if len(data) > settings.import_max_bytes:
        raise HTTPException(413, f"The file is larger than {settings.import_max_bytes // 1024} KB")
    try:
        raws = ci.parse_rows(data, format, kind, settings.import_max_rows)
    except ci.ImportFileError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return ci.run_rows(ctx, db, brand, user, kind, raws, "import", dry_run=dry_run)


@images.post("", status_code=201)
def upload_image(
    file: UploadFile = File(...),
    purpose: Literal["candidate_image", "app_icon"] = Query("candidate_image"),
    brand_id: int | None = None,
    user: User = Depends(require_writer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    brand = resolve_brand(db, brand_id)
    data = file.file.read(settings.candidate_image_max_bytes + 1)
    try:
        img = process_candidate_image(data, settings.candidate_image_max_bytes)
    except LogoError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    row = db.query(CandidateImage).filter_by(brand_id=brand.id, sha256=img.sha256).first()
    if row is None:
        rel = save_candidate_image(settings.data_dir, brand.id, img)
        row = CandidateImage(brand_id=brand.id, purpose=purpose, path=rel, format=img.format, width=img.width, height=img.height,
                             size_bytes=len(img.data), sha256=img.sha256, hashes=img.hashes, created_by=user.id)
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            row = db.query(CandidateImage).filter_by(brand_id=brand.id, sha256=img.sha256).one()
        audit.record(db, user, "candidate_image.uploaded", f"{img.format} {img.width}x{img.height}", f"sha256={img.sha256[:12]}")
        db.commit()
    return {"id": row.id, "format": row.format, "width": row.width, "height": row.height, "size_bytes": row.size_bytes, "sha256": row.sha256,
            "purpose": row.purpose, "url": f"/api/candidate-images/{row.id}/file",
            "note": "Re-encoded copy. Hash similarity to an official logo is a hint, not proof of reuse."}


@images.get("/{image_id}/file")
def get_image(image_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> FileResponse:
    row = db.get(CandidateImage, image_id)
    root = Path(settings.data_dir).resolve()
    path = (root / row.path).resolve() if row else None
    if row is None or path is None or root not in path.parents or not path.is_file():
        raise HTTPException(404, "Image not found")
    media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[row.format]
    return FileResponse(path, media_type=media, headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})


__all__ = ["images", "router"]

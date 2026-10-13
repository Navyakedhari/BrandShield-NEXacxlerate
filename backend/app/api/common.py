from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pipeline.scan_pipeline import ScanContext
from app.models import Brand
from app.services import data_mode


def get_ctx(request: Request) -> ScanContext:
    return request.app.state.scan_ctx


def resolve_brand(db: Session, brand_id: int | None) -> Brand:
    """`brand_id` is optional everywhere: it defaults to the organisation's (single) brand."""
    brand = db.get(Brand, brand_id) if brand_id else db.scalars(select(Brand).where(Brand.archived_at.is_(None)).order_by(Brand.id)).first()
    if brand is None or brand.archived_at is not None:
        raise HTTPException(404, "Brand not found" if brand_id else "No brand profile yet. Create one first.")
    return brand


def mode_of(db: Session, request: Request) -> str:
    return data_mode.effective_mode(db, request.app.state.settings)

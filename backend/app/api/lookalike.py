from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import resolve_brand
from app.config.deps import get_current_user, require_admin, require_writer
from app.core.pipeline import lookalike
from app.db.session import get_db
from app.models import LookalikeCase, User
from app.services import audit

router = APIRouter(prefix="/lookalike")


def clean_name(v: str) -> str:
    """Collapse whitespace and drop control characters."""
    return " ".join("".join(ch for ch in v if ch.isprintable() or ch.isspace()).split())


class TestBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand_id: int | None = None
    platform: str | None = None

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = clean_name(v)
        if not v:
            raise ValueError("Type a name to test")
        return v


class CaseBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    expected: Literal["flagged", "not_flagged", "official"]

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = clean_name(v)
        if not v:
            raise ValueError("Name is required")
        return v


def _case(c: LookalikeCase) -> dict:
    return {"id": c.id, "name": c.name, "expected": c.expected, "builtin": c.builtin, "source": c.source}


@router.post("/test")
def test_name(body: TestBody, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Runs the live matcher with the Settings rules. Pure: no side effects, safe to call on every keystroke."""
    brand = resolve_brand(db, body.brand_id)
    return lookalike.evaluate_name(db, brand, body.name, body.platform)


@router.get("/test-cases")
def list_cases(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    lookalike.ensure_default_cases(db)
    items = [_case(c) for c in db.scalars(select(LookalikeCase).order_by(LookalikeCase.id))]
    return {"total": len(items), "items": items}


@router.post("/test-cases", status_code=201)
def add_case(body: CaseBody, user: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    lookalike.ensure_default_cases(db)
    if db.scalar(select(LookalikeCase.id).where(LookalikeCase.name == body.name, LookalikeCase.expected == body.expected)):
        raise HTTPException(409, "This test case already exists")
    case = LookalikeCase(name=body.name, expected=body.expected, builtin=False, created_by=user.id)
    db.add(case)
    db.flush()
    audit.record(db, user, "lookalike.case_added", body.name, f"expected {body.expected}")
    db.commit()
    return _case(case)


@router.delete("/test-cases/{case_id}")
def delete_case(case_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    case = db.get(LookalikeCase, case_id)
    if case is None:
        raise HTTPException(404, "Test case not found")
    audit.record(db, user, "lookalike.case_removed", case.name, f"expected {case.expected}")
    db.delete(case)
    db.commit()
    return {"ok": True}


@router.post("/test-cases/run")
def run_cases(brand_id: int | None = None, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> dict:
    brand = resolve_brand(db, brand_id)
    result = lookalike.run_cases(db, brand)
    audit.record(db, user, "lookalike.cases_run", brand.name, result["summary"])
    db.commit()
    return result

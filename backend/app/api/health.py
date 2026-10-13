from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config.deps import require_admin
from app.db.session import get_db
from app.models import User
from app.services import audit
from app.services import health as svc
from app.services.canary import run_canary

router = APIRouter()
VERSION = svc.VERSION


def _checks(request: Request, db: Session) -> dict:
    # app.state.redis_factory is an optional test hook (a fake Redis); production uses the real client.
    factory = getattr(request.app.state, "redis_factory", None) or svc._redis_conn
    return svc.run_checks(db, request.app.state.settings, redis_factory=factory)


@router.get("/health")
@router.get("/api/health")
def health(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Readiness, unauthenticated: overall status and one word per component. 503 only when the database is down."""
    report = svc.public_report(_checks(request, db))
    return JSONResponse(report, status_code=503 if report["status"] == svc.DOWN else 200)


@router.get("/health/live")
def live() -> dict:
    """Liveness: the process answers. No dependency is touched (use for restart decisions, not for routing)."""
    return {"status": "ok", "version": VERSION}


@router.get("/api/health/details")
def health_details(request: Request, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> JSONResponse:
    """Per-component detail (collector reasons and ages, migration revision, worker count, free disk). Admin only."""
    report = svc.detailed_report(_checks(request, db), request.app.state.settings)
    return JSONResponse(report, status_code=503 if report["status"] == svc.DOWN else 200)


@router.post("/api/health/canary")
def canary(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Run the in-memory canary (planted fake brand, no network, no real data touched) and report pass/fail."""
    result = run_canary()
    audit.record(db, admin, "health.canary", "canary", "passed" if result["passed"] else "FAILED: " + "; ".join(result["failures"])[:250])
    db.commit()
    return result

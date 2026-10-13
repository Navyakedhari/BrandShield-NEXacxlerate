from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_ctx, mode_of
from app.config.deps import get_current_user, require_admin
from app.config.time import iso_z
from app.core.collectors.base import SOURCE_PLATFORMS
from app.core.collectors.orchestrator import ensure_sources
from app.core.pipeline.matching import DEFAULT_RULES, is_hit
from app.core.pipeline.scan_pipeline import ScanContext
from app.db.session import get_db
from app.models import Brand, Detection, Setting, User
from app.services import app_settings, audit, data_mode
from app.services.scheduler import schedule_status

router = APIRouter(prefix="/settings")


class MonitoringIn(BaseModel):
    schedule: Literal["off", "5m", "15m", "1h", "1d"] | None = None
    sources: dict[str, bool] | None = None
    sensitivity: int | None = Field(default=None, ge=app_settings.SENSITIVITY_MIN, le=app_settings.SENSITIVITY_MAX)
    rules: dict[str, bool] | None = None
    impersonation_words: list[str] | None = None

    @field_validator("sources")
    @classmethod
    def _sources(cls, v):
        if v is None:
            return v
        bad = [k for k in v if k not in SOURCE_PLATFORMS]
        if bad:
            raise ValueError(f"Unknown platform: {', '.join(bad)}")
        return v

    @field_validator("rules")
    @classmethod
    def _rules(cls, v):
        if v is None:
            return v
        bad = [k for k in v if k not in DEFAULT_RULES]
        if bad:
            raise ValueError(f"Unknown rule: {', '.join(bad)}")
        return v

    @field_validator("impersonation_words")
    @classmethod
    def _words(cls, v):
        if v is None:
            return v
        out: list[str] = []
        for w in v:
            w = " ".join(w.lower().split())
            if not w:
                continue
            if len(w) > 30:
                raise ValueError("Each word must be at most 30 characters")
            if w not in out:
                out.append(w)
        if len(out) > 50:
            raise ValueError("At most 50 impersonation words")
        return out


class NotificationsIn(BaseModel):
    email_alerts: bool | None = None
    high_risk_immediate: bool | None = None
    weekly_digest: bool | None = None
    webhook_url: str | None = Field(default=None, max_length=500)

    @field_validator("webhook_url")
    @classmethod
    def _url(cls, v):
        if v is None or v == "":
            return v
        if not v.startswith("https://") or " " in v:
            raise ValueError("Webhook URL must start with https:// (or be empty)")
        return v


class SettingsIn(BaseModel):
    data_mode: Literal["live", "sample", "auto"] | None = None
    monitoring: MonitoringIn | None = None
    notifications: NotificationsIn | None = None


class PreviewBody(BaseModel):
    sensitivity: int = Field(ge=app_settings.SENSITIVITY_MIN, le=app_settings.SENSITIVITY_MAX)


def _view(db: Session, request: Request, ctx: ScanContext, user: User) -> dict:
    mon = app_settings.get_monitoring(db)
    notif = app_settings.get_notifications(db)
    rows = ensure_sources(db)
    row = db.scalar(select(Setting).where(Setting.key.in_(("monitoring", "notifications"))).order_by(Setting.updated_at.desc()))
    brand = db.scalars(select(Brand).where(Brand.archived_at.is_(None)).order_by(Brand.id)).first()
    sched = schedule_status(ctx, db)
    sel_mode = mode_of(db, request)
    webhook = notif["webhook_url"]
    return {
        "data_mode": data_mode.get_data_mode(db),
        "effective_data_mode": sel_mode,
        "monitoring": {
            "schedule": mon["schedule"],
            "sources": {r.platform: r.monitored for r in rows},
            "sensitivity": mon["sensitivity"],
            "threshold": app_settings.threshold_for(mon["sensitivity"]),
            "rules": mon["rules"],
            "impersonation_words": mon["impersonation_words"],
        },
        "scheduler": {**sched, "next_scan_at": iso_z(sched["next_scan_at"]) if sched["next_scan_at"] else None},
        # Notifications are stored preferences only: Phase 4 sends nothing anywhere.
        "notifications": {**notif, "webhook_url": webhook if user.role == "admin" else "", "webhook_configured": bool(webhook),
                          "delivery": "not_implemented"},
        "account": {"organization": brand.name if brand else "", "user": {"name": user.name, "email": user.email, "role": user.role}},
        "updated_at": iso_z(row.updated_at) if row else None,
    }


@router.get("")
def get_settings(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db), ctx: ScanContext = Depends(get_ctx)) -> dict:
    return _view(db, request, ctx, user)


@router.put("")
def put_settings(
    body: SettingsIn, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db), ctx: ScanContext = Depends(get_ctx)
) -> dict:
    changed: list[str] = []
    if body.monitoring is not None:
        mon = app_settings.get_monitoring(db)
        new = {**mon, "rules": dict(mon["rules"])}
        m = body.monitoring
        if m.schedule is not None and m.schedule != mon["schedule"]:
            new["schedule"] = m.schedule
            changed.append("monitoring.schedule")
        if m.sensitivity is not None and m.sensitivity != mon["sensitivity"]:
            new["sensitivity"] = m.sensitivity
            changed.append("monitoring.sensitivity")
        if m.rules is not None:
            for k, val in m.rules.items():
                if new["rules"][k] != val:
                    new["rules"][k] = val
                    changed.append(f"monitoring.rules.{k}")
        if m.impersonation_words is not None and m.impersonation_words != mon["impersonation_words"]:
            new["impersonation_words"] = m.impersonation_words
            changed.append("monitoring.impersonation_words")
        if any(c != "monitoring.sources" for c in changed):
            app_settings.put_setting(db, "monitoring", new, user)
        if m.sources is not None:
            rows = {r.platform: r for r in ensure_sources(db)}
            for platform, on in m.sources.items():
                if rows[platform].monitored != on:
                    rows[platform].monitored = on
                    audit.record(db, user, "source.monitor_toggled", platform, f"monitored={str(on).lower()}")
                    changed.append(f"monitoring.sources.{platform}")
    if body.notifications is not None:
        cur = app_settings.get_notifications(db)
        new_n = dict(cur)
        for k, val in body.notifications.model_dump(exclude_none=True).items():
            if new_n[k] != val:
                new_n[k] = val
                changed.append(f"notifications.{k}")
        if new_n != cur:
            app_settings.put_setting(db, "notifications", new_n, user)
    if changed:
        audit.record(db, user, "settings.updated", "settings", "changed: " + ", ".join(sorted(set(changed))))
    db.commit()
    if body.data_mode is not None and body.data_mode != data_mode.get_data_mode(db):
        data_mode.set_data_mode(db, body.data_mode, user)  # audited as settings.data_mode_changed
    return _view(db, request, ctx, user)


def _message(sensitivity: int) -> str:
    if sensitivity < 60:
        return "Strict: only the clearest look-alikes are sent to review."
    if sensitivity <= 80:
        return "A balanced setting for most teams."
    return "Sensitive: more weaker look-alikes are sent to review, so expect more false positives."


@router.post("/sensitivity-preview")
def sensitivity_preview(
    body: PreviewBody, request: Request, _: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    """How many stored candidates (current data mode) would be sent to review at this sensitivity. Read-only."""
    mode = mode_of(db, request)
    threshold = app_settings.threshold_for(body.sensitivity)
    rows = db.scalars(select(Detection).where(Detection.source == mode, Detection.status != "marked_official")).all()

    would = sum(1 for d in rows if is_hit(d.score, threshold, d.reasons or [], ((d.details or {}).get("name_match") or {}).get("verdict"),
                                           lookalike=((d.details or {}).get("domain") or {}).get("verdict") == "lookalike"))
    return {"sensitivity": body.sensitivity, "threshold": threshold, "would_review": would, "total": len(rows), "data_mode": mode,
            "is_sample": mode == "sample", "message": _message(body.sensitivity)}

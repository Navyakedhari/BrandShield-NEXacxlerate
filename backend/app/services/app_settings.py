"""Org-level settings stored in the `settings` table: monitoring and notifications (data_mode lives in data_mode.py)."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.time import utcnow
from app.core.pipeline.matching import DEFAULT_IMPERSONATION_WORDS, DEFAULT_RULES
from app.models import Setting, User

SCHEDULES = ("off", "5m", "15m", "1h", "1d")
SCHEDULE_MINUTES = {"off": 0, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}
SENSITIVITY_MIN, SENSITIVITY_MAX = 50, 95

DEFAULT_MONITORING: dict[str, Any] = {
    "schedule": "1d",
    "sensitivity": 75,
    "rules": dict(DEFAULT_RULES),
    "impersonation_words": list(DEFAULT_IMPERSONATION_WORDS),
}
DEFAULT_NOTIFICATIONS: dict[str, Any] = {
    "email_alerts": False,
    "high_risk_immediate": False,
    "weekly_digest": False,
    "webhook_url": "",
}


def _row(db: Session, key: str) -> Setting | None:
    return db.scalar(select(Setting).where(Setting.key == key))


def _merged(defaults: dict[str, Any], stored: Any) -> dict[str, Any]:
    out = copy.deepcopy(defaults)
    if isinstance(stored, dict):
        for k, v in stored.items():
            if k in out:
                if isinstance(out[k], dict) and isinstance(v, dict):
                    out[k] = {**out[k], **{kk: vv for kk, vv in v.items() if kk in out[k]}}
                else:
                    out[k] = v
    return out


def get_monitoring(db: Session) -> dict[str, Any]:
    row = _row(db, "monitoring")
    return _merged(DEFAULT_MONITORING, row.value if row else None)


def get_notifications(db: Session) -> dict[str, Any]:
    row = _row(db, "notifications")
    return _merged(DEFAULT_NOTIFICATIONS, row.value if row else None)


def put_setting(db: Session, key: str, value: Any, user: User | None) -> None:
    row = _row(db, key)
    if row is None:
        db.add(Setting(key=key, value=value, updated_by=user.id if user else None, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_by = user.id if user else None
        row.updated_at = utcnow()


def get_raw(db: Session, key: str) -> Any:
    row = _row(db, key)
    return row.value if row else None


def threshold_for(sensitivity: int) -> int:
    """Minimum score for a detection. Higher sensitivity = lower bar. 75 -> 25, 50 -> 50, 95 -> 5."""
    return 100 - int(sensitivity)


def interval_minutes(settings_interval: int | None, monitoring: dict[str, Any]) -> tuple[int, str]:
    """(minutes, source). The SCAN_INTERVAL_MINUTES env value wins over the Settings page; 0 = off."""
    if settings_interval is not None:
        return max(0, int(settings_interval)), "env"
    return SCHEDULE_MINUTES.get(monitoring.get("schedule", "1d"), 1440), "setting"

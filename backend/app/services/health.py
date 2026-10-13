"""Readiness checks behind /health and /api/health/details.

Rules:
* Never make a live third-party call. Collector status comes from configuration (key present, package installed,
  base URL set) and from the state the last scan stored (status, reason, age).
* Never leak a secret or a URL with credentials: only booleans, short reason codes and ages leave this module.
* Overall status: `down` only when the database is unreachable (HTTP 503); `degraded` when anything else is not
  fully healthy (HTTP 200: the app still serves); otherwise `ok`.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config.config import SERVER_DIR, Settings
from app.config.time import as_utc, utcnow
from app.models import CollectorState

log = logging.getLogger("app.health")
VERSION = "0.1.0"
OK, DEGRADED, DOWN = "ok", "degraded", "down"
NOT_CONFIGURED = "not_configured"
# Component states that make the overall status `degraded`.
_BAD = {DEGRADED, DOWN}
MIN_FREE_MB = 100


def _c(status: str, **detail: Any) -> dict:
    return {"status": status, **detail}


def check_database(db: Session) -> dict:
    try:
        db.execute(text("SELECT 1"))
        return _c(OK)
    except Exception as exc:  # noqa: BLE001
        log.warning("health: database check failed (%s)", type(exc).__name__)
        return _c(DOWN, error=type(exc).__name__)


def expected_head() -> str | None:
    """The newest Alembic revision shipped with this build, or None when the alembic folder is not available."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        script_dir = SERVER_DIR / "alembic"
        if not script_dir.is_dir():
            return None
        cfg = Config()
        cfg.set_main_option("script_location", str(script_dir))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001
        return None


def check_migration(db: Session, settings: Settings, head: str | None | Callable[[], str | None] = expected_head) -> dict:
    expected = head() if callable(head) else head
    try:
        row = db.execute(text("SELECT version_num FROM alembic_version")).first()
        current = row[0] if row else None
    except Exception:  # noqa: BLE001 - table missing: schema made without Alembic (tests, create_all) or never migrated
        db.rollback()
        # In production a database that was not migrated is a deployment error. Elsewhere (tests, e2e) tables come from create_all.
        return _c(DEGRADED if settings.app_env == "production" else "unknown", current=None, expected=expected, note="alembic_version table not found")
    if expected is None:
        return _c("unknown", current=current, expected=None, note="migration scripts not available in this build")
    if current == expected:
        return _c(OK, current=current, expected=expected)
    return _c(DEGRADED, current=current, expected=expected, note="run: alembic upgrade head")


def _redis_conn(settings: Settings):
    from redis import Redis  # lazy: the app starts without the package/server

    assert settings.redis_url is not None
    return Redis.from_url(settings.redis_url.get_secret_value(), socket_connect_timeout=1, socket_timeout=1)


def check_redis(settings: Settings, conn_factory: Callable[[Settings], Any] = _redis_conn) -> dict:
    if not settings.redis_url:
        return _c(NOT_CONFIGURED, note="REDIS_URL not set: scans run in the API process")
    try:
        conn_factory(settings).ping()
        return _c(OK)
    except Exception as exc:  # noqa: BLE001 - never include the exception text: it may contain the URL
        return _c(DOWN, error=type(exc).__name__)


def check_worker(settings: Settings, conn_factory: Callable[[Settings], Any] = _redis_conn) -> dict:
    """RQ workers registered in Redis for the scan queue (each worker refreshes its heartbeat while alive)."""
    if not settings.redis_url:
        return _c(NOT_CONFIGURED, note="no Redis: scans run in the API process")
    try:
        from rq import Queue, Worker

        conn = conn_factory(settings)
        queue = Queue(settings.scan_queue_name, connection=conn)
        workers = Worker.all(queue=queue)
        now = utcnow()
        beats = [as_utc(w.last_heartbeat) for w in workers if getattr(w, "last_heartbeat", None)]
        age = int((now - max(beats)).total_seconds()) if beats else None
        queued = queue.count
    except Exception as exc:  # noqa: BLE001
        return _c(DEGRADED, error=type(exc).__name__, note="could not read workers from Redis")
    if not workers:
        return _c(DEGRADED, workers=0, queued=queued, note="no worker registered: queued scans will not run")
    return _c(OK, workers=len(workers), queued=queued, last_heartbeat_age_seconds=age)


def check_data_dir(settings: Settings) -> dict:
    path: Path = settings.data_dir
    probe = path / f".health-{uuid.uuid4().hex}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
    except Exception as exc:  # noqa: BLE001
        return _c(DOWN, writable=False, error=type(exc).__name__)
    try:
        free_mb = int(shutil.disk_usage(path).free / (1024 * 1024))
    except OSError:
        return _c(OK, writable=True)
    if free_mb < MIN_FREE_MB:
        return _c(DEGRADED, writable=True, free_mb=free_mb, note=f"less than {MIN_FREE_MB} MB free")
    return _c(OK, writable=True, free_mb=free_mb)


def _age(ts: datetime | None, now: datetime) -> int | None:
    return int((now - as_utc(ts)).total_seconds()) if ts else None


def check_collectors(
    db: Session, settings: Settings, *, now: datetime | None = None, google_play_installed: Callable[[], bool] | None = None
) -> dict:
    """Per collector: configured? last known status, reason and age. Based on config and stored state only."""
    from app.core.collectors.selection import select_collectors
    from app.services import data_mode

    now = now or utcnow()
    mode = data_mode.get_data_mode(db)
    extra = {"google_play_installed": google_play_installed} if google_play_installed else {}
    selection = select_collectors(settings, mode, **extra)
    states = {s.collector: s for s in db.scalars(select(CollectorState))}
    items: dict[str, dict] = {}
    for e in selection.entries:
        st = states.get(e.name)
        item: dict[str, Any] = {"configured": e.configured, "active": e.active}
        if st is not None:
            item.update(
                last_status=st.status, last_reason=st.reason, last_attempt_age_seconds=_age(st.last_attempt_at, now),
                last_success_age_seconds=_age(st.last_success_at, now), consecutive_failures=st.consecutive_failures,
            )
        if e.name == "sample":
            item["status"] = OK
        elif not e.active:
            item["status"] = NOT_CONFIGURED  # optional collector that is switched off (e.g. SearXNG without a base URL)
        elif not e.configured:
            item["status"] = DEGRADED
            item["reason"] = e.reason.split(":")[0][:80]
        elif st is not None and st.status == "unavailable":
            item["status"] = DEGRADED
            item["reason"] = st.reason or "unavailable"
        else:
            item["status"] = OK
        items[e.name] = item
    live = [i for i in items.values() if i["active"] and i["status"] != NOT_CONFIGURED]
    usable = [i for i in live if i["status"] == OK]
    # Even with no usable source the app is up (scans end 'failed'), so this is never 'down'.
    status = OK if not live or len(usable) == len(live) else DEGRADED
    return {"status": status, "data_mode": mode, "effective_mode": selection.effective_mode, "sources": items}


def run_checks(db: Session, settings: Settings, *, redis_factory: Callable[[Settings], Any] = _redis_conn) -> dict[str, dict]:
    checks: dict[str, dict] = {"database": check_database(db)}
    if checks["database"]["status"] == DOWN:
        skipped = _c("unknown", note="database is down")
        checks["migration"] = checks["collectors"] = skipped
    else:
        checks["migration"] = check_migration(db, settings)
        try:
            checks["collectors"] = check_collectors(db, settings)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            checks["collectors"] = _c(DEGRADED, error=type(exc).__name__)
    checks["redis"] = check_redis(settings, redis_factory)
    checks["worker"] = check_worker(settings, redis_factory)
    checks["data_dir"] = check_data_dir(settings)
    return checks


def overall(checks: dict[str, dict]) -> str:
    if checks["database"]["status"] == DOWN:
        return DOWN
    return DEGRADED if any(c["status"] in _BAD for c in checks.values()) else OK


def public_report(checks: dict[str, dict]) -> dict:
    """What an unauthenticated caller sees: component names and states only, no details."""
    return {"status": overall(checks), "version": VERSION, "database": {"status": checks["database"]["status"]},
            "components": {k: v["status"] for k, v in checks.items()}}


def detailed_report(checks: dict[str, dict], settings: Settings) -> dict:
    return {"status": overall(checks), "version": VERSION, "environment": settings.app_env, "checks": checks}

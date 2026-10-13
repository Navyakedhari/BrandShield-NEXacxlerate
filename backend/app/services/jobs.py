"""Background execution of scans.

* `rq`    : Redis + RQ worker (`python -m app.worker`). Used when REDIS_URL is set (SCAN_EXECUTOR=auto|rq).
* `thread`: in-process daemon thread. The fallback when REDIS_URL is unset, so local dev works without Redis.
* `inline`: run synchronously in the caller. Used by the tests.

If Redis cannot be reached when a scan is queued, the scan falls back to a thread (logged) instead of hanging.
`rq` and `redis` are imported lazily, so the app starts and the tests run without a Redis server.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.config.config import Settings

log = logging.getLogger("app.jobs")
WORKER_FUNC = "app.worker.run_scan_job"


def resolve_executor(settings: Settings) -> str:
    mode = (settings.scan_executor or "auto").lower()
    if mode == "auto":
        return "rq" if settings.redis_url else "thread"
    if mode == "rq" and not settings.redis_url:
        log.warning("SCAN_EXECUTOR=rq but REDIS_URL is not set: using the in-process thread runner")
        return "thread"
    return mode if mode in ("rq", "thread", "inline") else "thread"


class JobDispatcher:
    def __init__(self, settings: Settings, run: Callable[[int], None]) -> None:
        self.settings = settings
        self.run = run
        self.executor = resolve_executor(settings)

    def enqueue(self, scan_id: int) -> str:
        """Start a queued scan. Returns the executor that took it."""
        if self.executor == "inline":
            self.run(scan_id)
            return "inline"
        if self.executor == "rq":
            try:
                self._enqueue_rq(scan_id)
                return "rq"
            except Exception as exc:  # noqa: BLE001 - Redis down, rq missing ...
                log.warning("could not queue scan %s in Redis (%s): running it in-process", scan_id, type(exc).__name__)
        threading.Thread(target=self._safe_run, args=(scan_id,), name=f"scan-{scan_id}", daemon=True).start()
        return "thread"

    def _safe_run(self, scan_id: int) -> None:
        try:
            self.run(scan_id)
        except Exception:  # noqa: BLE001 - run_scan already records failures; this is the last line of defence
            log.exception("scan thread %s crashed", scan_id)

    def _enqueue_rq(self, scan_id: int) -> None:
        from redis import Redis  # lazy
        from rq import Queue

        assert self.settings.redis_url is not None
        conn = Redis.from_url(self.settings.redis_url.get_secret_value())
        Queue(self.settings.scan_queue_name, connection=conn).enqueue(
            WORKER_FUNC, scan_id, job_timeout=self.settings.scan_job_timeout_seconds, job_id=f"scan-{scan_id}"
        )

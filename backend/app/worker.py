"""RQ worker entrypoint.

Run (from server/, with REDIS_URL set):   python -m app.worker

The API queues `app.worker.run_scan_job(scan_id)` on the queue named SCAN_QUEUE_NAME. Without REDIS_URL the API
runs scans in-process instead, so this module is only needed when Redis is used. Not exercised against a real
Redis server in the automated tests (see docs/DEPENDENCIES.md).
"""

from __future__ import annotations

import logging
import os
import sys

from app.config.config import get_settings
from app.config.logging import configure_logging
from app.config.observability import init_sentry
from app.core.collectors.selection import CollectorRuntime
from app.core.pipeline.scan_pipeline import ScanContext, run_scan
from app.db.session import make_engine, make_session_factory
from app.services.context import build_context

log = logging.getLogger("app.worker")
_ctx: ScanContext | None = None


def get_context() -> ScanContext:
    """One context per worker process (engine, collector runtime, fetcher)."""
    global _ctx
    if _ctx is None:
        settings = get_settings()
        configure_logging(settings.log_level, settings.secret_values)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        sf = make_session_factory(make_engine(settings.database_url))
        _ctx = build_context(settings, sf, CollectorRuntime(settings, sf))
    return _ctx


def run_scan_job(scan_id: int) -> None:
    """The function RQ calls. run_scan never raises: failures are recorded on the scan row."""
    run_scan(get_context(), scan_id)


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.secret_values)
    init_sentry(settings)
    if not settings.redis_url:
        log.error("REDIS_URL is not set: nothing to work on (the API runs scans in-process without Redis).")
        return 2
    from redis import Redis  # lazy
    from rq import Queue, SimpleWorker, Worker

    conn = Redis.from_url(settings.redis_url.get_secret_value())
    queue = Queue(settings.scan_queue_name, connection=conn)
    # RQ's default Worker forks, which Windows does not support: use SimpleWorker there.
    worker_cls = SimpleWorker if os.name == "nt" else Worker
    log.info("worker listening on queue %s", settings.scan_queue_name)
    worker_cls([queue], connection=conn).work()
    return 0


if __name__ == "__main__":
    sys.exit(main())

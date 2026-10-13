from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.config.time import utcnow
from app.db.base import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), unique=True)  # slug, same as the platform enum
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(10))  # social | store
    description: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(15), default="online")  # online | slow | unavailable
    monitored: Mapped[bool] = mapped_column(Boolean, default=True)
    message: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[str] = mapped_column(String(200), default="")
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_in_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stale_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="live")


class CollectorState(Base):
    """Per-collector health, back-off state and the last good raw result (the cache shown when it is down)."""

    __tablename__ = "collector_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collector: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(15), default="online")  # online | slow | unavailable
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)  # SourceUnavailable.reason
    message: Mapped[str] = mapped_column(String(400), default="")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cache_brand_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache: Mapped[list | None] = mapped_column(JSON, nullable=True)  # last good normalized candidates
    cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="live")  # live | sample


_ACTIVE_SCAN = text("state IN ('queued', 'running')")
_BUILTIN_CASE = text("builtin")


class Scan(Base):
    __tablename__ = "scans"
    # One queued/running scan per brand, enforced by the database (partial unique index; SQLite and PostgreSQL both support it).
    __table_args__ = (
        Index("uq_scans_one_active_per_brand", "brand_id", unique=True, sqlite_where=_ACTIVE_SCAN, postgresql_where=_ACTIVE_SCAN),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), unique=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(10), default="all")  # all | social | apps
    # queued | running | completed | partial | failed  (API also exposes job.state: queued|running|done|error)
    state: Mapped[str] = mapped_column(String(10), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(300), default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    data_mode: Mapped[str] = mapped_column(String(10), default="live")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_by: Mapped[str] = mapped_column(String(120), default="scheduler")  # user name or 'scheduler'
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    source_report: Mapped[list] = mapped_column(JSON, default=list)  # one entry per collector
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)  # short messages, never secrets
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """Org-level key/value settings (data_mode, schedule, sensitivity, rules, ...)."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    format: Mapped[str] = mapped_column(String(5))  # csv | html (pdf is not built: no PDF library is installed)
    title: Mapped[str] = mapped_column(String(200), default="Brand Protection Report")
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | approved (a human approves; nothing is sent)
    detection_ids: Mapped[list] = mapped_column(JSON, default=list)
    snapshot: Mapped[list] = mapped_column(JSON, default=list)  # rows as they were when the report was created
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    includes_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="live")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiUsage(Base):
    """Monthly credit usage per external data provider."""

    __tablename__ = "api_usage"
    __table_args__ = (UniqueConstraint("provider", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(60))
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    credits: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    """Append-only record of who did what."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # no FK: log outlives users
    actor: Mapped[str] = mapped_column(String(120), default="system")
    action: Mapped[str] = mapped_column(String(60), index=True)
    target: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="live")


class LookalikeCase(Base):
    """A labelled look-alike test case (default 12 plus any an admin adds)."""

    __tablename__ = "lookalike_cases"
    # The built-in cases are seeded once: a second concurrent seeder hits this index instead of inserting 12 more rows.
    __table_args__ = (
        Index("uq_lookalike_cases_builtin_name", "name", unique=True, sqlite_where=_BUILTIN_CASE, postgresql_where=_BUILTIN_CASE),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    expected: Mapped[str] = mapped_column(String(15))  # flagged | not_flagged | official
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="live")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TextAnalysisCache(Base):
    """Persistent cache of validated hosted-model answers (providers/text/cache.py). Key = hash of model + prompt version + context + text."""

    __tablename__ = "text_analysis_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

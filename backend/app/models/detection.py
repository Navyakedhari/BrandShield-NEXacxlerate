from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.config.time import utcnow
from app.db.base import Base


class Detection(Base):
    """A suspected impersonation. A human must confirm every one (status starts as 'new')."""

    __tablename__ = "detections"
    __table_args__ = (UniqueConstraint("brand_id", "canonical_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # fake_profile | fake_brand_page | scam_account | fake_app | lookalike_domain
    category: Mapped[str] = mapped_column(String(10))  # social | apps | web
    platform: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    handle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    package_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(200), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    score: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(10))  # high | medium | low
    # LOW | MEDIUM | HIGH | CRITICAL, beside band (engine/risk_engine.py)
    severity: Mapped[str] = mapped_column(String(10), default="LOW", server_default="LOW")
    algorithm_version: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "1.0" social/app, "2.0" web
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # new | reviewing | confirmed | dismissed | marked_official | cleared (below threshold but near-name: false-positive guard)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    # Investigation outcome: true_positive | false_positive | resolved | monitoring (null = none recorded)
    resolution: Mapped[str | None] = mapped_column(String(20), nullable=True)
    canonical_key: Mapped[str] = mapped_column(String(400), default="")  # "<platform>:<lower-case handle or package or url>"
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scan_id: Mapped[int | None] = mapped_column(ForeignKey("scans.id", ondelete="SET NULL"), nullable=True)
    score_history: Mapped[list] = mapped_column(JSON, default=list)  # [{"at", "score", "scan_id"}]
    notes: Mapped[list] = mapped_column(JSON, default=list)  # [{"at", "actor", "text", "kind"}]
    # [{"signal": str, "detail": str, "points": int}] - every score is explainable
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    # {"page_url", "captured_at", "sha256", "snapshot_path", "screenshot_path"}; sha256 = hash of the saved snapshot
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    details: Mapped[dict] = mapped_column(JSON, default=dict)  # per-type extras (followers, downloads, ...)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="live")  # live | sample
    # Where the candidate came from: scan (a collector) | manual (typed in) | import (CSV/JSON) | synthetic (sample data)
    origin: Mapped[str] = mapped_column(String(12), default="scan", server_default="scan")
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OfficialMatch(Base):
    """An item a scan skipped because it matched the allowlist (shown as 'Excluded as official')."""

    __tablename__ = "official_matches"
    __table_args__ = (UniqueConstraint("brand_id", "match_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    scan_id: Mapped[int | None] = mapped_column(ForeignKey("scans.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(10))  # account | app
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    label: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(String(40))  # exact_handle | exact_url | package_id | apple_id | bundle_id | marked_official
    matched_rule: Mapped[str] = mapped_column(String(300), default="")  # human readable rule that matched
    match_key: Mapped[str] = mapped_column(String(400), default="")  # "<kind>:<platform>:<key>", one row per asset
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    official_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("official_accounts.id", ondelete="SET NULL"), nullable=True
    )
    official_app_id: Mapped[int | None] = mapped_column(
        ForeignKey("official_apps.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(10), default="live")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DetectionEvent(Base):
    """Append-only history of one detection (shown as 'history' in the detail panel)."""

    __tablename__ = "detection_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    detection_id: Mapped[int] = mapped_column(ForeignKey("detections.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="status")  # status | scan | note | evidence
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class CandidateImage(Base):
    """An image a person uploaded for a manual / imported candidate or an app icon. Re-encoded, hashed, stored under a hash-derived name.
    Hash similarity to an official logo is a hint, not proof of reuse."""

    __tablename__ = "candidate_images"
    __table_args__ = (UniqueConstraint("brand_id", "sha256"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20), default="candidate_image")  # candidate_image | app_icon
    path: Mapped[str] = mapped_column(String(300))
    format: Mapped[str] = mapped_column(String(10))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    hashes: Mapped[dict] = mapped_column(JSON, default=dict)  # {"phash": hex, "dhash": hex, "whash": hex, "colorhash": hex}
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

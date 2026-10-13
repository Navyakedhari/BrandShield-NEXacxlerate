"""Runs the collector chain. A failing source never blocks a scan.

For every collector: honour back-off, call it, time it, store the last good result (cache), and on
SourceUnavailable record why, schedule the next retry, and serve the cached results (marked stale, with age).
Per-platform status is then written to the `sources` table (online | slow | unavailable).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config.time import as_utc, utcnow
from app.core.collectors.base import (
    SOCIAL_PLATFORMS,
    SOURCE_PLATFORMS,
    STORE_PLATFORMS,
    WEB_PLATFORM,
    BrandQuery,
    Candidate,
    CollectContext,
    Collector,
    SourceUnavailable,
    dedupe,
)
from app.models import Brand, CollectorState, Source

log = logging.getLogger("app.core.collectors")

# Back-off after consecutive failures: 1 min, 5 min, 15 min, 1 hour, then 6 hours (cap). Resets on success.
BACKOFF_SCHEDULE_SECONDS = (60, 300, 900, 3600, 21600)
MAX_PROVIDER_RETRY_AFTER = 24 * 3600
# Problems fixed by editing local configuration: re-check on the next scan, no waiting period.
LOCAL_CONFIG_REASONS = frozenset({"no_key", "not_installed", "not_configured", "unsafe_base_url"})

SOURCE_DEFAULTS = {
    "instagram": ("Instagram", "social", "Public profile and page search"),
    "facebook": ("Facebook", "social", "Public page search"),
    "x": ("X", "social", "Public profile search"),
    "telegram": ("Telegram", "social", "Public channels and groups"),
    "linkedin": ("LinkedIn", "social", "Public company page search"),
    "google_play": ("Google Play", "store", "Store search and listing details"),
    "app_store": ("Apple App Store", "store", "Store search and listing details"),
    "third_party_apk": ("Third-party APK sites", "store", "Known APK mirror sites (coverage is limited)"),
}


def backoff_delay(consecutive_failures: int) -> int:
    """Seconds to wait before retrying after N consecutive failures (N >= 1), capped at the last step."""
    n = max(1, consecutive_failures)
    return BACKOFF_SCHEDULE_SECONDS[min(n, len(BACKOFF_SCHEDULE_SECONDS)) - 1]


def retry_delay(reason: str, consecutive_failures: int, retry_after: float | None) -> int | None:
    """Seconds until the next automatic retry, or None for 'next scan'."""
    if reason in LOCAL_CONFIG_REASONS:
        return None
    if retry_after is not None and retry_after > 0:
        return int(min(max(retry_after, 1), MAX_PROVIDER_RETRY_AFTER))
    return backoff_delay(consecutive_failures)


@dataclass
class SourceReport:
    collector: str
    status: str  # online | slow | unavailable | skipped
    platforms: tuple[str, ...] = ()
    reason: str | None = None
    message: str = ""
    latency_ms: int | None = None
    candidates: int = 0
    from_cache: bool = False
    cached_at: datetime | None = None
    cached_age_seconds: int | None = None
    next_retry_at: datetime | None = None
    consecutive_failures: int = 0
    in_backoff: bool = False
    warnings: list[str] = field(default_factory=list)
    data_source: str = "live"

    def as_dict(self) -> dict:
        return {
            "collector": self.collector,
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "candidates": self.candidates,
            "from_cache": self.from_cache,
            "cached_age_seconds": self.cached_age_seconds,
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
            "consecutive_failures": self.consecutive_failures,
            "in_backoff": self.in_backoff,
            "warnings": list(self.warnings),
            "data_source": self.data_source,
        }


@dataclass
class RunResult:
    candidates: list[Candidate]
    reports: list[SourceReport]

    @property
    def unavailable(self) -> list[str]:
        return [r.collector for r in self.reports if r.status == "unavailable"]

    @property
    def partial(self) -> list[str]:
        return [r.collector for r in self.reports if r.status == "slow"]


# ---------------------------------------------------------------------------------------------------------
# Brand snapshot


def load_brand_query(db: Session, brand_id: int | None = None, *, logo_bytes: Callable[[Brand], bytes | None] | None = None) -> BrandQuery | None:
    brand = db.get(Brand, brand_id) if brand_id else db.scalars(select(Brand).where(Brand.archived_at.is_(None)).order_by(Brand.id)).first()
    if brand is None:
        return None
    handles: dict[str, list[str]] = {}
    for a in brand.accounts:
        handles.setdefault(a.platform, []).append(a.handle)
    return BrandQuery(
        brand_id=brand.id,
        name=brand.name,
        aliases=tuple(brand.aliases or ()),
        keywords=tuple(brand.keywords or ()),
        domains=tuple(brand.domains or ()),
        official_handles={k: tuple(v) for k, v in handles.items()},
        official_apps=tuple((a.name, a.package_id, a.publisher) for a in brand.apps),
        publisher_names=tuple({a.publisher for a in brand.apps if a.publisher}),
        logo_bytes=logo_bytes(brand) if logo_bytes else None,
    )


# ---------------------------------------------------------------------------------------------------------
# Sources table


def ensure_sources(db: Session) -> list[Source]:
    existing = {s.platform: s for s in db.scalars(select(Source))}
    created = False
    for platform in SOURCE_PLATFORMS:
        if platform not in existing:
            name, kind, desc = SOURCE_DEFAULTS[platform]
            row = Source(platform=platform, name=name, kind=kind, description=desc, status="online", monitored=True,
                         message="Not checked yet.", detail="", source="live")
            db.add(row)
            existing[platform] = row
            created = True
    if created:
        db.commit()
    return [existing[p] for p in SOURCE_PLATFORMS]


def _scope_platforms(scope: str) -> frozenset[str]:
    if scope == "social":
        return frozenset(p for p in SOURCE_PLATFORMS if p in SOCIAL_PLATFORMS)
    if scope == "apps":
        return frozenset(p for p in SOURCE_PLATFORMS if p in STORE_PLATFORMS)
    return frozenset(SOURCE_PLATFORMS)


def _state(db: Session, name: str, data_source: str) -> CollectorState:
    st = db.scalar(select(CollectorState).where(CollectorState.collector == name))
    if st is None:
        st = CollectorState(collector=name, status="online", message="", consecutive_failures=0, source=data_source)
        db.add(st)
        db.flush()
    return st


def _age(now: datetime, then: datetime | None) -> int | None:
    return max(0, int((now - as_utc(then)).total_seconds())) if then else None


def _cached(st: CollectorState, brand_id: int, platforms: frozenset[str], now: datetime) -> list[Candidate]:
    if not st.cache or st.cache_brand_id != brand_id:
        return []
    out = []
    for d in st.cache:
        try:
            c = Candidate.from_dict(d)
        except (TypeError, ValueError, KeyError):
            continue
        if c.platform in platforms or c.platform == "youtube" or (c.platform == WEB_PLATFORM and WEB_PLATFORM in platforms):
            c.stale = True
            c.cached_at = as_utc(st.cached_at) if st.cached_at else None
            out.append(c)
    return out


# ---------------------------------------------------------------------------------------------------------
# The run


def run_collectors(
    session_factory: sessionmaker[Session],
    brand: BrandQuery,
    collectors: Sequence[Collector],
    *,
    scope: str = "all",
    only_platforms: frozenset[str] | None = None,
    force: bool = False,
    max_queries: int | None = None,
    slow_threshold_ms: int = 4000,
    now: Callable[[], datetime] = utcnow,
    perf: Callable[[], float] = time.perf_counter,
    url_checker=None,
    reachability_checks: int = 0,
) -> RunResult:
    """Run the chain; never raises because of a collector. `force` ignores back-off (manual retry)."""
    with session_factory() as db:
        ensure_sources(db)
        monitored = {s.platform for s in db.scalars(select(Source)) if s.monitored}
    platforms = _scope_platforms(scope) & monitored
    if only_platforms is not None:
        platforms &= only_platforms
    elif scope == "all":
        platforms = platforms | {WEB_PLATFORM}  # web candidates have no Source Health row: they ride along with a full scan
    ctx = CollectContext(platforms=platforms, max_queries=max_queries, now=now)
    reports: list[SourceReport] = []
    merged: list[Candidate] = []

    for col in collectors:
        covered = tuple(p for p in col.platforms if p in platforms)
        data_source = "sample" if col.name == "sample" else "live"
        if not covered:
            reports.append(SourceReport(col.name, "skipped", col.platforms, message="No monitored platforms in this scan scope.", data_source=data_source))
            continue
        report, cands = _run_one(session_factory, brand, col, covered, ctx, force, slow_threshold_ms, now, perf, data_source)
        reports.append(report)
        merged.extend(cands)

    merged = [c for c in merged if c.platform in platforms or (c.platform == "youtube" and scope != "apps")]
    merged = dedupe(merged)
    if url_checker is not None and reachability_checks > 0:
        enrich_reachability(merged, url_checker, reachability_checks)
    with session_factory() as db:
        _write_sources(db, reports, merged, platforms, now())
    return RunResult(merged, reports)


def _run_one(session_factory, brand, col: Collector, covered, ctx, force, slow_ms, now, perf, data_source):
    t_now = now()
    report = SourceReport(col.name, "online", covered, data_source=data_source)
    with session_factory() as db:
        st = _state(db, col.name, data_source)
        st.source = data_source
        if not force and st.next_retry_at is not None and as_utc(st.next_retry_at) > t_now:
            cands = _cached(st, brand.brand_id, ctx.platforms, t_now)
            report.status = "unavailable"
            report.reason = st.reason
            report.message = f"{st.message} Waiting to retry (back-off)."
            report.in_backoff = True
            report.next_retry_at = as_utc(st.next_retry_at)
            report.consecutive_failures = st.consecutive_failures
            report.from_cache = bool(cands)
            report.cached_at = as_utc(st.cached_at) if st.cached_at and cands else None
            report.cached_age_seconds = _age(t_now, st.cached_at) if cands else None
            report.candidates = len(cands)
            db.commit()
            return report, cands

        # Release the write lock taken when the state row was created or changed. A collector may write to the
        # database through its own session (Tavily's credit ledger does); on SQLite that would otherwise fail with
        # "database is locked" and the whole source would be reported as an internal error.
        db.commit()
        started = perf()
        failure: SourceUnavailable | None = None
        cands: list[Candidate] = []
        try:
            cands = col.collect(brand, ctx)
        except SourceUnavailable as exc:
            failure = exc
        except Exception:  # noqa: BLE001 - a buggy collector must not stop the scan
            log.exception("collector %s crashed", col.name)
            failure = SourceUnavailable("internal_error", f"Unexpected error in the {col.name} collector. See the server log.")
        latency = int((perf() - started) * 1000)
        t_done = now()
        st.last_attempt_at = t_done
        st.latency_ms = latency
        report.latency_ms = latency

        if failure is None:
            warnings = list(col.warnings)
            slow = latency > slow_ms
            st.status = "slow" if (slow or warnings) else "online"
            st.reason = "partial" if warnings else ("slow" if slow else None)
            st.message = " ".join(warnings) if warnings else (f"Responding slowly ({latency} ms)." if slow else "Working normally.")
            st.consecutive_failures = 0
            st.next_retry_at = None
            st.last_success_at = t_done
            st.cache = [c.to_dict() for c in cands]
            st.cache_brand_id = brand.brand_id
            st.cached_at = t_done
            report.status, report.reason, report.message, report.warnings = st.status, st.reason, st.message, warnings
            report.candidates = len(cands)
            db.commit()
            return report, cands

        st.consecutive_failures += 1
        st.status = "unavailable"
        st.reason = failure.reason
        st.message = failure.message[:400]
        delay = retry_delay(failure.reason, st.consecutive_failures, failure.retry_after)
        st.next_retry_at = t_done + timedelta(seconds=delay) if delay is not None else None
        cached = _cached(st, brand.brand_id, ctx.platforms, t_done)
        report.status, report.reason, report.message = "unavailable", failure.reason, st.message
        report.next_retry_at = as_utc(st.next_retry_at) if st.next_retry_at else None
        report.consecutive_failures = st.consecutive_failures
        report.from_cache = bool(cached)
        report.cached_at = as_utc(st.cached_at) if st.cached_at and cached else None
        report.cached_age_seconds = _age(t_done, st.cached_at) if cached else None
        report.candidates = len(cached)
        db.commit()
        return report, cached


def _write_sources(db: Session, reports: list[SourceReport], cands: list[Candidate], platforms: frozenset[str], t_now: datetime) -> None:
    rows = {s.platform: s for s in db.scalars(select(Source))}
    for platform in platforms:
        row = rows.get(platform)
        rel = [r for r in reports if r.status != "skipped" and platform in r.platforms]
        if row is None or not rel:
            continue
        online = [r for r in rel if r.status == "online"]
        slow = [r for r in rel if r.status == "slow"]
        found = sum(1 for c in cands if c.platform == platform and not c.stale)
        if online or slow:
            best = (online or slow)[0]
            row.status = "online" if online else "slow"
            row.message = "Working normally." if online else best.message
            row.last_sync = t_now
            row.stale_since = None
            row.retry_in_minutes = None
            unit = "apps" if platform in STORE_PLATFORMS else "profiles"
            row.detail = f"{found} {unit} found"
        else:
            first = rel[0]
            row.status = "unavailable"
            row.stale_since = next((r.cached_at for r in rel if r.cached_at), None)
            msg = first.message
            if row.stale_since:
                msg += f" Showing cached results from {as_utc(row.stale_since).strftime('%d %b %H:%M')} UTC."
            row.message = msg[:300]
            row.detail = "Cached results shown" if row.stale_since else "No results"
            retries = [r.next_retry_at for r in rel if r.next_retry_at]
            row.retry_in_minutes = max(1, math.ceil((min(retries) - t_now).total_seconds() / 60)) if retries else None
        row.source = "sample" if all(r.data_source == "sample" for r in (online or slow or rel)) else "live"
    db.commit()


def enrich_reachability(cands: list[Candidate], checker, limit: int) -> None:
    """Check result URLs with the SSRF-safe UrlChecker (services/url_safety.py). Sample data is never fetched."""
    done = 0
    for c in cands:
        if done >= limit:
            break
        if c.data_source == "sample" or c.stale or not c.profile_url:
            continue
        res = checker.check(c.profile_url)
        c.extras["reachable"] = bool(res.ok)
        c.extras["reachability_message"] = res.message
        done += 1


# ---------------------------------------------------------------------------------------------------------
# Read-side helpers used by the API


def collector_states(db: Session) -> dict[str, CollectorState]:
    return {s.collector: s for s in db.scalars(select(CollectorState))}

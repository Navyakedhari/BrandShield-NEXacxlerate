"""The scan pipeline.

collect (Phase 3 orchestrator) -> de-duplicate -> allowlist exclusion -> name match (display name AND handle)
-> prefilter -> SSRF-safe logo fetch + similarity -> bio / description analysis (+ optional LLM) -> score
-> upsert detections.

Rules this module enforces:
* Allowlist first: an exact official handle / URL / package id is dropped BEFORE any scoring and recorded in
  `official_matches` with the reason and the matched rule. The brand name alone never excludes anything.
* A human decision survives a re-scan: confirmed / dismissed / reviewing / marked_official detections are never
  reset or demoted. A re-scan only adds evidence, `last_seen_at` and a score-history point (and a note when the
  score moved materially).
* Candidates below the threshold that still look like the brand's name are stored as status "cleared" (the
  false-positive guard list). Nothing is ever sent or reported automatically; humans approve every detection.
* A failing source never blocks a scan: the scan ends `partial` and everything the other sources found is saved.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config.config import Settings
from app.config.logging import Redactor
from app.config.time import as_utc, iso_z
from app.core.collectors.base import Candidate
from app.core.collectors.orchestrator import RunResult, ensure_sources, load_brand_query, run_collectors
from app.core.collectors.selection import CollectorRuntime, select_collectors
from app.core.engine.domain_match import DomainAnalysis, analyze_domain, make_domain_matcher
from app.core.engine.image_hashes import ImageHashSimilarity, MultiHashSimilarity, best_match
from app.core.engine.llm import LLMIntentResult, ScamIntentLLM
from app.core.engine.logo_similarity import HashSimilarity, NotAvailable, load_image
from app.core.engine.models import BrandProfileInput
from app.core.engine.name_match import SAFE, NameMatchResult
from app.core.engine.pipeline import _package_name_text, best_name_match
from app.core.engine.risk_engine import RiskAssessment, RiskConfig, RiskEngine
from app.core.engine.scoring import ScoreResult, handle_from_url
from app.core.engine.urls import parse_url
from app.core.pipeline import evidence
from app.core.pipeline.image_fetch import FetchError, SafeFetcher
from app.core.pipeline.matching import (
    BRAND_SIGNALS,
    BrandAllowlist,
    OfficialHit,
    apply_rules,
    build_matcher,
    is_hit,
    official_assets,
    reason_dicts,
    summary_and_tags,
    technique_labels,
)
from app.core.pipeline.screenshot import ScreenshotProvider
from app.core.pipeline.text_analysis import TextResult, build_text_service
from app.core.providers.base import ReputationResult
from app.models import Brand, CandidateImage, Detection, DetectionEvent, OfficialMatch, Scan, Source, User
from app.services import app_settings, audit, data_mode
from app.services.validators import display_handle

log = logging.getLogger("app.scan")

HUMAN_STATES = frozenset({"reviewing", "confirmed", "dismissed", "marked_official"})
ACTIVE_STATES = ("queued", "running")
FINISHED_STATES = ("completed", "partial", "failed")
MATERIAL_DELTA = 10  # points; a re-scan score change at least this big on a decided detection adds a note
NEAR_NAME_PCT = 25
INTAKE_SOURCES = frozenset({"manual", "import"})  # typed-in / imported candidates: never fetched, prefilter off


def origin_of(c: Candidate) -> str:
    return "synthetic" if c.source == "sample" else (c.source if c.source in INTAKE_SOURCES else "scan")


class ScanAlreadyRunning(Exception):
    def __init__(self, scan: Scan) -> None:
        super().__init__("A scan is already running")
        self.scan = scan


class NoBrand(Exception):
    pass


@dataclass
class ScanContext:
    """Everything a scan needs. Built by the API app and by the worker process."""

    settings: Settings
    session_factory: sessionmaker[Session]
    runtime: CollectorRuntime
    fetcher: SafeFetcher
    screenshot: ScreenshotProvider | None = None
    llm: ScamIntentLLM | None = None
    dispatcher: Any = None  # JobDispatcher, set by whoever builds the context
    perf: Callable[[], float] = time.perf_counter
    text_providers: dict = field(default_factory=dict, repr=False)  # provider name -> provider (keeps its quota cool-down)

    def now(self) -> datetime:
        return self.runtime.now()


# ---------------------------------------------------------------------------------------------------------
# starting a scan


def fail_stale_scans(db: Session, brand_id: int, stale_minutes: int, now: datetime) -> None:
    """A queued/running scan that has not finished for a long time belongs to a dead worker: fail it so scans can start."""
    for s in db.scalars(select(Scan).where(Scan.brand_id == brand_id, Scan.state.in_(ACTIVE_STATES))):
        age = (now - as_utc(s.started_at or s.created_at)).total_seconds() / 60
        if age > stale_minutes:
            s.state, s.finished_at = "failed", now
            s.message = "The scan did not finish (worker stopped?) and was marked failed."
            s.errors = [*(s.errors or []), "stale: no progress for a long time"]
    db.flush()


def running_scan(db: Session, brand_id: int) -> Scan | None:
    return db.scalars(select(Scan).where(Scan.brand_id == brand_id, Scan.state.in_(ACTIVE_STATES)).order_by(Scan.id.desc())).first()


def request_scan(ctx: ScanContext, db: Session, *, brand_id: int | None, scope: str, user: User | None) -> Scan:
    """Create a queued scan (audited) and hand it to the job dispatcher. Raises ScanAlreadyRunning / NoBrand."""
    brand = db.get(Brand, brand_id) if brand_id else db.scalars(select(Brand).where(Brand.archived_at.is_(None)).order_by(Brand.id)).first()
    if brand is None or brand.archived_at is not None:
        raise NoBrand()
    now = ctx.now()
    fail_stale_scans(db, brand.id, ctx.settings.scan_stale_minutes, now)
    active = running_scan(db, brand.id)
    if active is not None:
        db.commit()
        raise ScanAlreadyRunning(active)
    selection = select_collectors(ctx.settings, data_mode.get_data_mode(db))
    scan = Scan(
        job_id=f"job_{int(now.timestamp() * 1000):x}{brand.id:x}",
        brand_id=brand.id,
        scope=scope,
        state="queued",
        progress=0,
        message="Queued",
        data_mode=selection.effective_mode,
        created_by=user.id if user else None,
        started_by=user.name if user else "scheduler",
        created_at=now,
    )
    db.add(scan)
    try:
        db.flush()
    except IntegrityError as exc:
        # Another API process queued a scan for this brand between our check and our insert
        # (partial unique index uq_scans_one_active_per_brand, migration 0004).
        db.rollback()
        active = running_scan(db, brand.id)
        if active is None:
            raise
        raise ScanAlreadyRunning(active) from exc
    audit.record(db, user, "scan.started", f"{brand.name} ({scope})", f"job {scan.job_id}, data mode {selection.effective_mode}",
                 actor_label="scheduler", source="sample" if selection.effective_mode == "sample" else "live")
    db.commit()
    if ctx.dispatcher is not None:
        ctx.dispatcher.enqueue(scan.id)
    return scan


# ---------------------------------------------------------------------------------------------------------
# helpers


def canonical_key(c: Candidate) -> str:
    h = (c.handle or "").strip().lstrip("@").lower()
    if not h and c.profile_url:
        p = parse_url(c.profile_url)
        h = (p.canonical_url if p.ok and p.canonical_url else c.profile_url).lower()
    return f"{c.platform}:{h}"[:400]


def _canon_url(c: Candidate) -> str | None:
    if not c.profile_url:
        return None
    p = parse_url(c.profile_url)
    return (p.canonical_url if p.ok and p.canonical_url else c.profile_url).lower().rstrip("/")


def dedupe_candidates(cands: list[Candidate]) -> tuple[list[Candidate], int]:
    """Same platform+handle/package, or the same canonical URL, is one candidate. Later copies fill gaps."""
    by_key: dict[str, Candidate] = {}
    by_url: dict[str, Candidate] = {}
    removed = 0
    for c in cands:
        key = canonical_key(c)
        url = _canon_url(c)
        keep = by_key.get(key) or (by_url.get(url) if url else None)
        if keep is None:
            by_key[key] = c
            if url:
                by_url[url] = c
            continue
        removed += 1
        for f in ("display_name", "bio", "snippet", "publisher", "icon_url", "rating", "rating_count", "followers", "following", "posts"):
            if getattr(keep, f) is None and getattr(c, f) is not None:
                setattr(keep, f, getattr(c, f))
        if keep.stale and not c.stale:
            keep.stale = False
    return list(by_key.values()), removed


def _kw_hit(brand: Brand, c: Candidate) -> bool:
    terms = [k.strip().lstrip("#").lower() for k in (brand.keywords or []) if len(k.strip().lstrip("#")) >= 3]
    terms += [d.lower() for d in (brand.domains or [])]
    if not terms:
        return False
    hay = " ".join(x for x in (c.display_name, c.handle, c.title, c.bio, c.snippet) if x).lower()
    return any(t in hay for t in terms)


def _near_name(name: NameMatchResult) -> bool:
    return name.verdict != SAFE or name.name_match_pct >= NEAR_NAME_PCT


def _detection_type(c: Candidate, reasons: list[dict]) -> str:
    if c.kind == "web":
        return "lookalike_domain"
    if c.kind == "app":
        return "fake_app"
    if any(r["signal"] == "bio_signals" and r["points"] >= 6 for r in reasons):
        return "scam_account"
    return "fake_brand_page" if c.platform in ("facebook", "linkedin") else "fake_profile"


@dataclass
class Outcome:
    candidate: Candidate
    kind: str  # hit | cleared | ignored
    score: ScoreResult | None = None
    name: NameMatchResult | None = None
    matched_text: str | None = None
    reasons: list[dict] = field(default_factory=list)
    logo: dict = field(default_factory=dict)
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    threshold: int = 25
    llm_used: bool = False
    assessment: RiskAssessment | None = None
    analysis: DomainAnalysis | None = None  # web candidates
    reputation: ReputationResult | None = None
    text: TextResult | None = None


def _count_text(text: TextResult, counts: dict) -> None:
    if text.api is None:
        return
    if text.api.ok:
        counts["llm_checked"] += 1
    elif text.api.reason not in ("no_key", "no_model", "no_text", "disabled", "not_installed"):
        counts["llm_errors"] += 1


class _Env:
    """Per-scan, read-only inputs shared by every candidate."""

    def __init__(self, ctx: ScanContext, db: Session, brand: Brand) -> None:
        mon = app_settings.get_monitoring(db)
        self.threshold = app_settings.threshold_for(mon["sensitivity"])
        self.rules = mon["rules"]
        self.matcher = build_matcher(brand, words=mon["impersonation_words"])
        self.allow = BrandAllowlist(brand)
        self.assets = official_assets(brand)
        self.brand_profile = BrandProfileInput(name=brand.name, aliases=list(brand.aliases or []), description=brand.description,
                                               extra_impersonation_words=list(mon["impersonation_words"]))
        st = ctx.settings
        self.engine = RiskEngine(RiskConfig(st.severity_medium_min, st.severity_high_min, st.severity_critical_min), now=ctx.now)
        self.domain_matcher = make_domain_matcher(brand.name, list(brand.aliases or []), list(brand.domains or []), words=list(mon["impersonation_words"]))
        self.official_domains = list(brand.domains or [])
        self.aliases = list(brand.aliases or [])
        self.reputation: dict[str, ReputationResult] = {}  # canonical URL -> Safe Browsing result (web candidates, live only)
        self.reputation_note: dict[str, str] = {}  # canonical URL -> why it was not checked
        self.official_logos = _load_brand_logos(ctx.settings, brand)  # [(label, image)]: the primary logo and every extra one
        self.image_impl, self.image_backend = _image_impl(ctx.settings)
        self.db = db
        self.data_dir = ctx.settings.data_dir
        self.logo_fetches = 0
        self.text = build_text_service(ctx, db)
        self.llm_note = self.text.note
        self.brand_description = brand.description
        self.today = ctx.now().date()


def _load_brand_logos(settings: Settings, brand: Brand) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    rows = [("primary logo", brand.logo_path), *[(lg.label or f"logo {lg.id}", lg.path) for lg in brand.logos]]
    for label, rel in rows:
        path = evidence.safe_path(settings.data_dir, rel)
        if path is None:
            continue
        try:
            out.append((label, load_image(path.read_bytes())))
        except Exception:  # noqa: BLE001 - a broken logo file just means "logo unavailable"
            continue
    return out


def _image_impl(settings: Settings):
    """Backend for logo scoring. Default `hash` = the original pHash + dHash (scores unchanged)."""
    name = settings.image_similarity_backend
    if name == "multihash":
        return MultiHashSimilarity(), "image-2"
    if name == "imagehash":
        try:
            return ImageHashSimilarity(), "image-2-imagehash"
        except NotAvailable:
            log.warning("IMAGE_SIMILARITY_BACKEND=imagehash but ImageHash is not installed: using the default hashes")
    return HashSimilarity(), "image-1"


def _compare(env: _Env, img) -> tuple[float, str | None]:
    return best_match(img, env.official_logos, env.image_impl)


def _intake_image(env: _Env, c: Candidate):
    """The uploaded image of a manual / imported candidate (by id), or None."""
    image_id = c.extras.get("image_id")
    row = env.db.get(CandidateImage, image_id) if isinstance(image_id, int) else None
    path = evidence.safe_path(env.data_dir, row.path) if row is not None else None
    if path is None:
        return None
    try:
        return load_image(path.read_bytes())
    except Exception:  # noqa: BLE001
        return None


def _read_logo_bytes(settings: Settings, brand: Brand) -> bytes | None:
    path = evidence.safe_path(settings.data_dir, brand.logo_path)
    try:
        return path.read_bytes() if path is not None else None
    except OSError:
        return None


def _logo_step(ctx: ScanContext, env: _Env, c: Candidate, counts: dict) -> tuple[float | None, dict]:
    """SSRF-safe fetch + similarity. A failure NEVER fails the candidate: it is recorded as 'logo unavailable'."""
    what = "icon" if c.kind == "app" else "profile picture"

    def unavailable(why: str) -> tuple[None, dict]:
        counts["logo_unavailable"] += 1
        return None, {"status": "unavailable", "similarity": None, "reason": f"Logo unavailable: {why}"}

    if c.data_source == "sample":
        # Sample candidates never touch the network. They may carry generated image bytes (derived from the brand's
        # own uploaded logo); without them the honest answer stays "unavailable". Non-sample candidates never get here.
        if not c.inline_image:
            return unavailable("this sample candidate has no generated image (no brand logo has been uploaded)")
        if not env.official_logos:
            return unavailable("no official logo has been uploaded for the brand")
        try:
            sim, _who = _compare(env, load_image(c.inline_image))
        except Exception:  # noqa: BLE001
            return unavailable(f"the generated sample {what} could not be compared")
        counts["logo_compared"] += 1
        return float(sim), {
            "status": "compared", "similarity": round(float(sim), 1), "reason": None,
            "origin": "generated_sample", "variant": c.extras.get("logo_variant"),
            "note": "Compared against a generated sample image, not a real downloaded picture.", "matched_logo": _who, "algorithm": env.image_backend,
        }
    if c.source in INTAKE_SOURCES:
        # Typed-in / imported rows: URLs are never visited. Only an image the analyst uploaded can be compared.
        if not env.official_logos:
            return unavailable("no official logo has been uploaded for the brand")
        img = _intake_image(env, c)
        if img is None:
            return unavailable(f"no {what} was uploaded for this {c.source} candidate (its URLs are never fetched)")
        try:
            sim, who = _compare(env, img)
        except Exception:  # noqa: BLE001
            return unavailable(f"the uploaded {what} could not be compared")
        counts["logo_compared"] += 1
        return float(sim), {"status": "compared", "similarity": round(float(sim), 1), "reason": None, "origin": "uploaded", "matched_logo": who,
                            "algorithm": env.image_backend, "note": "Hash similarity is a hint that two pictures look alike, not proof of reuse."}
    if not ctx.settings.scan_logo_fetch:
        return unavailable("logo fetching is switched off")
    if not env.official_logos:
        return unavailable("no official logo has been uploaded for the brand")
    if not c.icon_url:
        return unavailable(f"the source gave no {what} URL")
    if c.stale:
        return unavailable("the listing is a cached copy")
    if env.logo_fetches >= ctx.settings.scan_max_logo_fetches:
        return unavailable("per-scan logo download limit reached")
    env.logo_fetches += 1
    try:
        img = ctx.fetcher.fetch_image(c.icon_url)
        sim, who = _compare(env, img)
    except FetchError as exc:
        return unavailable(f"{what} could not be fetched ({exc})")
    except Exception:  # noqa: BLE001
        return unavailable(f"{what} could not be compared")
    counts["logo_compared"] += 1
    return float(sim), {"status": "compared", "similarity": round(float(sim), 1), "reason": None, "origin": "fetched", "matched_logo": who,
                        "algorithm": env.image_backend, "note": "Hash similarity is a hint that two pictures look alike, not proof of reuse."}


def _reputation_pass(ctx: ScanContext, env: _Env, cands: list[Candidate], counts: dict) -> None:
    """Ask Safe Browsing about the candidate URLs that already look related to the brand. One batched request.

    Only live, fresh, non-official web candidates are sent: sample URLs are never sent to a provider, a cached copy is not
    re-checked, and unrelated domains are not sent at all (quota and privacy). The URLs are looked up, never opened."""
    to_check: list[str] = []
    for c in cands:
        if c.kind != "web" or not c.profile_url:
            continue
        counts["web_candidates"] += 1
        if env.allow.check(c) is not None:
            continue
        if c.data_source == "sample":
            env.reputation_note[c.profile_url] = "Sample data: the URL is not sent to Google."
            counts["reputation_skipped_sample"] += 1
            continue
        if c.stale:
            env.reputation_note[c.profile_url] = "The candidate is a cached copy; reputation was not re-checked."
            continue
        analysis = analyze_domain(c.profile_url, env.brand_profile.name, env.official_domains, aliases=env.aliases, matcher=env.domain_matcher)
        if not analysis.brand_related:
            env.reputation_note[c.profile_url] = "The domain does not resemble the brand, so it was not sent for a reputation check."
            continue
        if len(to_check) >= ctx.settings.safe_browsing_max_urls_per_scan:
            env.reputation_note[c.profile_url] = "Per-scan reputation lookup limit reached."
            counts["reputation_over_limit"] += 1
            continue
        to_check.append(c.profile_url)
    if not to_check:
        return
    results = ctx.runtime.safe_browsing.check_urls(to_check)
    env.reputation.update(results)
    for r in results.values():
        counts["reputation_checked" if r.checked_at else "reputation_not_checked"] += 1
        if r.verdict == "match":
            counts["reputation_match"] += 1
        elif r.verdict == "clean":
            counts["reputation_clean"] += 1
        else:
            counts["reputation_unavailable"] += 1


def _evaluate_web(env: _Env, c: Candidate, counts: dict) -> Outcome:
    analysis = analyze_domain(c.profile_url or c.handle or "", env.brand_profile.name, env.official_domains, aliases=env.aliases,
                              matcher=env.domain_matcher)
    name = analysis.name_match or NameMatchResult(SAFE, 0, notes=["No domain name to compare."])
    if analysis.url.reason or analysis.verdict == "not_comparable" or not analysis.brand_related:
        counts["prefiltered"] += 1
        return Outcome(c, "ignored", name=name, analysis=analysis)
    rep = env.reputation.get(c.profile_url or "")
    note = env.reputation_note.get(c.profile_url or "")
    assessment = env.engine.assess_web(analysis, rep, synthetic=c.data_source == "sample", reputation_note=note)
    counts["evaluated"] += 1
    reasons = reason_dicts(assessment.reasons)
    parts = [t.replace("_", " ") for t in name.techniques]
    if analysis.components.get("brand_in_subdomain"):
        parts.append("brand in the subdomain")
    if analysis.components.get("tld_swap"):
        parts.append("TLD swap")
    if any(r["signal"] == "idn_homograph" for r in reasons):
        parts.append("look-alike characters")
    if rep is not None and rep.verdict == "match":
        parts.append("listed by Google Safe Browsing")
    summary = (", ".join(parts) or f"domain resembles the brand ({name.name_match_pct}%)")[:300]
    tags = [p[:1].upper() + p[1:] for p in parts]
    hit = is_hit(assessment.score, env.threshold, reasons, name.verdict, lookalike=analysis.verdict == "lookalike")
    return Outcome(c, "hit" if hit else "cleared", assessment.score_result, name, analysis.url.registrable_domain, reasons, {}, summary, tags,
                   env.threshold, False, assessment, analysis, rep)


def evaluate(ctx: ScanContext, env: _Env, brand: Brand, c: Candidate, counts: dict) -> Outcome:
    if c.kind == "web":
        return _evaluate_web(env, c, counts)
    eng = c.to_engine()
    if c.kind == "social":
        texts = [c.handle, c.display_name, handle_from_url(c.profile_url)]
    else:
        texts = [c.display_name, _package_name_text(c.handle)]
    name, matched = best_name_match(env.matcher, texts)
    name = apply_rules(name, env.rules)
    near = _near_name(name)
    if ctx.settings.scan_prefilter and not near and not _kw_hit(brand, c) and c.source not in INTAKE_SOURCES:
        counts["prefiltered"] += 1
        return Outcome(c, "ignored", name=name)

    sim, logo = _logo_step(ctx, env, c, counts)
    llm_result: LLMIntentResult | None = None
    text: TextResult | None = None
    synthetic = c.data_source == "sample"
    if c.kind == "social":
        eng.logo_similarity = sim  # type: ignore[union-attr]
        if c.bio:
            text = env.text.analyze("bio", c.bio, env.brand_description, synthetic=synthetic, stale=c.stale)
            llm_result = text.llm if len(c.bio) >= 20 else None
            _count_text(text, counts)
        assessment = env.engine.assess_social(eng, env.assets, name, today=env.today, llm=llm_result, synthetic=synthetic)  # type: ignore[arg-type]
    else:
        eng.icon_similarity = sim  # type: ignore[union-attr]
        if c.bio:  # informational only: the app scorer has no AI input, so this adds no points
            text = env.text.analyze("app_description", c.bio, env.brand_description, synthetic=synthetic, stale=c.stale)
            _count_text(text, counts)
        assessment = env.engine.assess_app(eng, env.brand_profile, env.assets, name, today=env.today, publisher_matcher=env.matcher, synthetic=synthetic)  # type: ignore[arg-type]
    score = assessment.score_result
    counts["evaluated"] += 1
    reasons = reason_dicts(score.reasons)
    summary, tags = summary_and_tags(name, reasons, sim)
    kind = "hit" if is_hit(score.score, env.threshold, reasons, name.verdict) else ("cleared" if near else "ignored")
    oc = Outcome(c, kind, score, name, matched, reasons, logo, summary, tags, env.threshold, llm_result is not None, assessment)
    oc.text = text
    return oc


def _clear_reason(oc: Outcome) -> str:
    assert oc.score is not None and oc.name is not None
    bits = [f"Name partly resembles the brand ({oc.name.name_match_pct}%)"]
    if oc.logo.get("status") == "compared" and oc.logo.get("similarity") is not None:
        bits.append(f"logo {oc.logo['similarity']:.0f}% match")
    elif oc.logo.get("reason"):
        bits.append(oc.logo["reason"].lower())
    bits.append(f"score {oc.score.score} is below the review threshold of {oc.threshold}")
    if not any(r["signal"] in BRAND_SIGNALS and r["points"] > 0 for r in oc.reasons):
        bits.append("no other brand evidence")
    return "; ".join(bits) + "."


# ---------------------------------------------------------------------------------------------------------
# persistence


def _evidence_for(ctx: ScanContext, c: Candidate, now: datetime, previous: dict | None) -> dict:
    sha, rel = evidence.save_snapshot(ctx.settings.data_dir, evidence.snapshot_payload(c))
    ev = dict(previous or {})
    history = list(ev.pop("history", []) or [])
    if previous and previous.get("sha256") and previous.get("sha256") != sha:
        history.append({"captured_at": previous.get("captured_at"), "sha256": previous.get("sha256"), "snapshot_path": previous.get("snapshot_path")})
    ev.update({"page_url": c.profile_url, "captured_at": iso_z(now), "sha256": sha, "snapshot_path": rel})
    ev["history"] = history[-10:]
    if ctx.screenshot is not None and c.data_source != "sample" and not c.stale and c.profile_url:
        try:
            png = ctx.screenshot.capture(c.profile_url)
        except Exception:  # noqa: BLE001 - optional hook
            png = None
        if png:
            s_sha, s_rel = evidence.save_screenshot(ctx.settings.data_dir, png)
            ev["screenshot_path"], ev["screenshot_sha256"] = s_rel, s_sha
    ev.setdefault("screenshot_path", None)
    return ev


def _candidate_details(c: Candidate) -> dict:
    return {
        "followers": c.followers, "following": c.following, "posts": c.posts,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "verified": c.verified, "bio": c.bio, "rating": c.rating, "rating_count": c.rating_count,
        "store": c.store, "icon_url": c.icon_url, "stale": c.stale,
        "cached_at": iso_z(c.cached_at) if c.cached_at else None,
    }


def _details(oc: Outcome) -> dict:
    assert oc.score is not None and oc.name is not None
    n = oc.name
    d = {
        "summary": oc.summary,
        "tags": oc.tags,
        "matched_text": oc.matched_text,
        "name_match": {
            "verdict": n.verdict, "pct": n.name_match_pct, "techniques": n.techniques, "technique_labels": technique_labels(n),
            "squat_category": n.squat_category, "added_words": n.added_words, "impersonation_words": n.impersonation_words,
            "edit_distance": n.edit_distance, "notes": n.notes,
        },
        "logo": oc.logo,
        "raw_total": oc.score.raw_total,
        "capped": oc.score.capped,
        "threshold": oc.threshold,
        "candidate": _candidate_details(oc.candidate),
        "extras": oc.candidate.extras,
        "ai_text_check": oc.llm_used,
    }
    if oc.assessment is not None:
        d["signals"] = [sg.to_dict() for sg in oc.assessment.signals] + (oc.text.signals if oc.text else [])
        d["weights"] = oc.assessment.weights
    if oc.text is not None:
        d["text_analysis"] = oc.text.details()
    if oc.analysis is not None:
        d["domain"] = oc.analysis.to_dict()
        d["reputation"] = oc.reputation.to_dict() if oc.reputation is not None else {
            "verdict": "unavailable", "provider": "google_safe_browsing", "checked_at": None}
    if oc.kind == "cleared":
        d["clear_reason"] = _clear_reason(oc)
    return d


def _event(db: Session, det: Detection, now: datetime, *, kind: str, to: str | None = None, frm: str | None = None,
           note: str = "", actor: str = "system", actor_id: int | None = None) -> None:
    db.add(DetectionEvent(detection_id=det.id, at=now, actor=actor, actor_id=actor_id, kind=kind, from_status=frm, to_status=to, note=note))


def _push_history(det: Detection, score: int, scan: Scan | None, now: datetime) -> None:
    hist = list(det.score_history or [])
    if scan is None or not any(h.get("scan_id") == scan.id for h in hist):
        hist.append({"at": iso_z(now), "score": score, "scan_id": scan.id if scan else None})
        det.score_history = hist[-50:]


def _add_note(det: Detection, text: str, now: datetime, kind: str = "system") -> None:
    det.notes = [*(det.notes or []), {"at": iso_z(now), "actor": "system", "text": text, "kind": kind}][-50:]


def _apply_fields(det: Detection, oc: Outcome, now: datetime, status: str) -> None:
    c = oc.candidate
    prev_origins = (det.details or {}).get("origins", [])
    assert oc.score is not None
    det.type = _detection_type(c, oc.reasons)
    det.category = {"app": "apps", "web": "web"}.get(c.kind, "social")
    det.platform = c.platform
    det.name = (c.display_name or c.handle or c.title or "")[:200]
    det.handle = (c.handle or None) if c.kind in ("social", "web") else None
    det.package_id = c.handle if c.kind == "app" else None
    det.publisher = c.publisher
    det.profile_url = c.profile_url
    det.score = oc.score.score
    det.band = oc.score.band  # unchanged 85 / 70 cutoffs
    assert oc.assessment is not None
    det.severity, det.algorithm_version, det.scored_at = oc.assessment.severity, oc.assessment.algorithm_version, oc.assessment.scored_at
    det.reasons = oc.reasons
    det.details = _details(oc)
    det.details = {**det.details, "origins": sorted({*(prev_origins), origin_of(c)})}
    det.stale = c.stale
    det.source = c.data_source
    det.status = status


def upsert_detection(ctx: ScanContext, db: Session, scan: Scan | None, brand: Brand, oc: Outcome, existing: dict[str, Detection], counts: dict) -> None:
    now = ctx.now()
    c = oc.candidate
    assert oc.score is not None and oc.name is not None
    key = canonical_key(c)
    det = existing.get(key)
    new_status = "new" if oc.kind == "hit" else "cleared"
    if det is None:
        if oc.kind == "cleared" and not _near_name(oc.name):
            return
        det = Detection(brand_id=brand.id, canonical_key=key, found_at=now, first_seen_at=now, last_seen_at=now, last_scan_id=scan.id if scan else None, origin=origin_of(c),
                        score=oc.score.score, band=oc.score.band, type="", category="", platform=c.platform, status=new_status)
        _apply_fields(det, oc, now, new_status)
        det.evidence = _evidence_for(ctx, c, now, None)
        det.score_history = []
        det.notes = []
        _push_history(det, oc.score.score, scan, now)
        db.add(det)
        db.flush()
        _event(db, det, now, kind="scan", to=new_status, note=f"Found by scan {scan.job_id}." if scan else f"Added by {origin_of(c)} entry.")
        existing[key] = det
        counts["detections_new" if oc.kind == "hit" else "cleared_new"] += 1
        return

    old_score, old_status = det.score, det.status
    det.last_seen_at, det.last_scan_id = now, (scan.id if scan else det.last_scan_id)
    prev_evidence = det.evidence
    det.evidence = _evidence_for(ctx, c, now, prev_evidence)
    if det.evidence.get("sha256") != (prev_evidence or {}).get("sha256"):
        _event(db, det, now, kind="evidence", note="New evidence snapshot captured.")
    _push_history(det, oc.score.score, scan, now)

    if old_status in HUMAN_STATES:
        # Human decision survives: no status / score / reasons change. Only history, evidence and a note.
        counts["detections_protected"] += 1
        if abs(oc.score.score - old_score) >= MATERIAL_DELTA:
            _add_note(det, f"Re-scan scored this {oc.score.score} (was {old_score}). Status stays '{old_status}'; review again if needed.", now)
            _event(db, det, now, kind="note", note=f"Score changed from {old_score} to {oc.score.score} on re-scan. Status not changed.")
        return

    _apply_fields(det, oc, now, new_status)
    counts["detections_updated"] += 1
    if new_status != old_status:
        why = "Score rose above the review threshold." if new_status == "new" else "Score fell below the review threshold."
        _event(db, det, now, kind="status", frm=old_status, to=new_status, note=why)
        if new_status == "new":
            counts["detections_new"] += 1


def _record_official(db: Session, scan: Scan | None, brand: Brand, c: Candidate, hit: OfficialHit, now: datetime, seen: dict[str, OfficialMatch]) -> None:
    key = f"{hit.kind}:{c.platform}:{hit.value.lower()}"[:400]
    row = seen.get(key) or db.scalar(select(OfficialMatch).where(OfficialMatch.brand_id == brand.id, OfficialMatch.match_key == key))
    if c.kind == "social":
        label = f"{display_handle(c.platform, (c.handle or hit.value).lstrip('@'))} on {c.platform}"
    elif c.kind == "web":
        label = f"{c.handle or hit.value} (official domain {hit.value})"
    else:
        label = f"{c.display_name or hit.value} · {c.handle or hit.value}"
    if row is None:
        row = OfficialMatch(
            brand_id=brand.id, scan_id=scan.id if scan else None, kind=hit.kind, platform=c.platform, label=label[:300], reason=hit.reason,
            matched_rule=hit.rule[:300], match_key=key, url=(c.profile_url or None), last_seen_at=now, times_seen=1,
            official_account_id=hit.account_id, official_app_id=hit.app_id, source=c.data_source,
        )
        db.add(row)
    else:
        row.scan_id, row.last_seen_at, row.times_seen = (scan.id if scan else row.scan_id), now, (row.times_seen or 0) + 1
        row.url = c.profile_url or row.url
        row.official_account_id = hit.account_id or row.official_account_id
        row.official_app_id = hit.app_id or row.official_app_id
    seen[key] = row


# ---------------------------------------------------------------------------------------------------------
# the run


def _redact(ctx: ScanContext, text: str) -> str:
    return Redactor(ctx.settings.secret_values).redact(text)[:300]


def _set_progress(ctx: ScanContext, scan_id: int, progress: int, message: str, steps: list | None = None) -> None:
    with ctx.session_factory() as db:
        scan = db.get(Scan, scan_id)
        if scan is not None and scan.state == "running":
            scan.progress, scan.message = progress, message
            if steps is not None:
                scan.steps = steps
            db.commit()


def run_scan(ctx: ScanContext, scan_id: int) -> None:
    """Run one queued scan to completion. Never raises: a crash marks the scan failed with a short summary."""
    t0 = ctx.perf()
    with ctx.session_factory() as db:
        scan = db.get(Scan, scan_id)
        if scan is None or scan.state != "queued":
            return
        scan.state, scan.started_at, scan.progress, scan.message = "running", ctx.now(), 3, "Starting"
        db.commit()
    try:
        _execute(ctx, scan_id, t0)
    except Exception as exc:  # noqa: BLE001 - a crashing scan must end as 'failed', not stay 'running'
        log.exception("scan %s crashed", scan_id)
        with ctx.session_factory() as db:
            scan = db.get(Scan, scan_id)
            if scan is not None:
                scan.state, scan.progress, scan.finished_at = "failed", 100, ctx.now()
                scan.message = "The scan stopped because of an internal error."
                scan.errors = [*(scan.errors or []), _redact(ctx, f"internal error: {type(exc).__name__}")]
                scan.timings = {**(scan.timings or {}), "total_ms": int((ctx.perf() - t0) * 1000)}
                user = db.get(User, scan.created_by) if scan.created_by else None
                audit.record(db, user, "scan.finished", scan.job_id, "failed: internal error", actor_label="scheduler")
                db.commit()


def _step_rows(result: RunResult) -> list[dict]:
    return [
        {"source": r.collector, "state": {"online": "done", "slow": "done", "unavailable": "error", "skipped": "skipped"}.get(r.status, "done"),
         "status": r.status, "checked": r.candidates, "new_detections": 0, "message": r.message}
        for r in result.reports
    ]


def _execute(ctx: ScanContext, scan_id: int, t0: float) -> None:
    sf = ctx.session_factory
    with sf() as db:
        scan = db.get(Scan, scan_id)
        assert scan is not None
        brand_id, scope = scan.brand_id, scan.scope
        selection = select_collectors(ctx.settings, data_mode.get_data_mode(db))
        brand_query = load_brand_query(
            db, brand_id, logo_bytes=(lambda b: _read_logo_bytes(ctx.settings, b)) if selection.effective_mode == "sample" else None
        )
        if brand_query is None:
            raise RuntimeError("brand missing")
        collectors = ctx.runtime.build(selection)

    t_collect = ctx.perf()
    result = run_collectors(
        sf, brand_query, collectors, scope=scope, slow_threshold_ms=ctx.settings.source_slow_threshold_ms,
        now=ctx.runtime.now, url_checker=None, reachability_checks=0,
    )
    collect_ms = int((ctx.perf() - t_collect) * 1000)
    _set_progress(ctx, scan_id, 40, f"Collected {len(result.candidates)} candidates", _step_rows(result))

    counts: dict[str, int] = {k: 0 for k in (
        "candidates_found", "duplicates_removed", "unique_candidates", "official_excluded", "prefiltered", "evaluated", "detections_new",
        "detections_updated", "detections_protected", "cleared_new", "logo_compared", "logo_unavailable", "llm_checked", "llm_errors",
        "official_detections_closed", "truncated", "web_candidates", "reputation_checked", "reputation_not_checked", "reputation_match",
        "reputation_clean", "reputation_unavailable", "reputation_skipped_sample", "reputation_over_limit")}
    counts["candidates_found"] = len(result.candidates)
    cands, removed = dedupe_candidates(result.candidates)
    counts["duplicates_removed"] = removed
    if len(cands) > ctx.settings.scan_max_candidates:
        counts["truncated"] = len(cands) - ctx.settings.scan_max_candidates
        cands = cands[: ctx.settings.scan_max_candidates]
    counts["unique_candidates"] = len(cands)

    t_proc = ctx.perf()
    with sf() as db:
        scan = db.get(Scan, scan_id)
        brand = db.get(Brand, brand_id)
        assert scan is not None and brand is not None
        env = _Env(ctx, db, brand)
        env.allow.learn_apple_ids(result.candidates)  # before de-duplication: the iTunes and Tavily copies of one listing share a URL and merge
        _reputation_pass(ctx, env, cands, counts)
        existing = {d.canonical_key: d for d in db.scalars(select(Detection).where(Detection.brand_id == brand.id))}
        seen_official: dict[str, OfficialMatch] = {}
        n = max(1, len(cands))
        for i, c in enumerate(cands):
            now = ctx.now()
            hit = env.allow.check(c)
            if hit is not None:
                counts["official_excluded"] += 1
                _record_official(db, scan, brand, c, hit, now, seen_official)
                det = existing.get(canonical_key(c))
                if det is not None and det.status != "marked_official":
                    old = det.status
                    det.status = "marked_official"
                    _event(db, det, now, kind="status", frm=old, to="marked_official", note=f"Matches the allowlist ({hit.rule})")
                    counts["official_detections_closed"] += 1
                continue
            oc = evaluate(ctx, env, brand, c, counts)
            if oc.kind != "ignored":
                upsert_detection(ctx, db, scan, brand, oc, existing, counts)
            if (i + 1) % 25 == 0:
                db.commit()
                _set_progress(ctx, scan_id, 40 + int(55 * (i + 1) / n), f"Analysed {i + 1} of {len(cands)} candidates")
        db.commit()
    process_ms = int((ctx.perf() - t_proc) * 1000)
    _finish(ctx, scan_id, result, counts, env.llm_note, collect_ms, process_ms, t0)


def _finish(ctx: ScanContext, scan_id: int, result: RunResult, counts: dict, llm_note: str | None, collect_ms: int, process_ms: int, t0: float) -> None:
    active = [r for r in result.reports if r.status != "skipped"]
    unavailable = [r for r in active if r.status == "unavailable"]
    partial_slow = [r for r in active if r.status == "slow" and r.warnings]
    errors = [_redact(ctx, f"{r.collector}: {r.reason or 'unavailable'}: {r.message}") for r in unavailable]
    errors += [_redact(ctx, f"{r.collector}: partial results: {' '.join(r.warnings)}") for r in partial_slow]
    if llm_note:
        errors.append(llm_note)
    fresh = [c for c in result.candidates if not c.stale]
    if active and len(unavailable) == len(active) and not fresh:
        status = "failed"
    elif unavailable or partial_slow:
        status = "partial"
    else:
        status = "completed"
    with ctx.session_factory() as db:
        scan = db.get(Scan, scan_id)
        assert scan is not None
        ensure_sources(db)
        in_scope = [s for s in db.scalars(select(Source)) if s.monitored and (scan.scope == "all" or (scan.scope == "social") == (s.kind == "social"))]
        now = ctx.now()
        scan.state, scan.progress, scan.finished_at = status, 100, now
        scan.counts = counts
        scan.source_report = [r.as_dict() for r in result.reports]
        scan.steps = _step_rows(result)
        scan.errors = errors
        scan.timings = {"collect_ms": collect_ms, "process_ms": process_ms, "total_ms": int((ctx.perf() - t0) * 1000)}
        scan.result = {
            "sources_checked": len(in_scope),
            "sources_online": sum(s.status == "online" for s in in_scope),
            "accounts_or_apps_checked": counts["unique_candidates"],
            "new_detections": counts["detections_new"],
            "official_excluded": counts["official_excluded"],
            "partial_sources": sorted(s.platform for s in in_scope if s.status == "slow"),
            "unavailable_sources": sorted(s.platform for s in in_scope if s.status == "unavailable"),
            "finished_at": iso_z(now),
        }
        scan.message = {
            "completed": "Scan finished",
            "partial": "Scan finished with some sources unavailable. Results from the other sources were saved.",
            "failed": "No data source was available, so nothing could be checked.",
        }[status]
        user = db.get(User, scan.created_by) if scan.created_by else None
        audit.record(db, user, "scan.finished", scan.job_id,
                     f"{status}: {counts['unique_candidates']} candidates, {counts['detections_new']} new, {counts['official_excluded']} official excluded",
                     actor_label="scheduler", source="sample" if scan.data_mode == "sample" else "live")
        db.commit()


# ---------------------------------------------------------------------------------------------------------
# views


def scan_view(scan: Scan) -> dict:
    """Public shape of a scan / job. `state` keeps the older job vocabulary (queued|running|done|error)."""
    job_state = {"completed": "done", "partial": "done", "failed": "error"}.get(scan.state, scan.state)

    def iso(d):
        return iso_z(d) if d else None

    return {
        "id": scan.id,
        "job_id": scan.job_id,
        "kind": "scan",
        "brand_id": scan.brand_id,
        "scope": scan.scope,
        "status": scan.state,
        "state": job_state,
        "progress": scan.progress,
        "message": scan.message,
        "steps": scan.steps or [],
        "result": scan.result,
        "counts": scan.counts or {},
        "source_report": scan.source_report or [],
        "timings": scan.timings or {},
        "errors": scan.errors or [],
        "started_by": scan.started_by,
        "data_mode": scan.data_mode,
        "is_sample": scan.data_mode == "sample",
        "created_at": iso(scan.created_at),
        "started_at": iso(scan.started_at),
        "finished_at": iso(scan.finished_at),
    }


__all__ = [
    "ACTIVE_STATES", "FINISHED_STATES", "HUMAN_STATES", "NoBrand", "ScanAlreadyRunning", "ScanContext", "canonical_key", "dedupe_candidates",
    "fail_stale_scans", "request_scan", "run_scan", "running_scan", "scan_view", "upsert_detection",
]

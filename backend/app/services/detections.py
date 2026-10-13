"""Detection queries, serialization and the human review actions (confirm / keep reviewing / dismiss / mark official).

Every action is audited with the actor and the note. Nothing here sends or reports anything anywhere: a person
decides, and the system only records the decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.config.time import iso_z, utcnow
from app.core.collectors.base import Candidate
from app.core.pipeline import evidence
from app.core.pipeline.matching import BrandAllowlist
from app.models import Brand, Detection, DetectionEvent, OfficialAccount, OfficialApp, OfficialMatch, User
from app.services import audit
from app.services.validators import build_account_url, display_handle

TYPES = ("fake_profile", "fake_brand_page", "scam_account", "fake_app")  # the four original types (social + apps)
WEB_TYPES = ("lookalike_domain",)  # category "web": look-alike domains / websites
ALL_TYPES = (*TYPES, *WEB_TYPES)
CATEGORIES = ("social", "apps", "web")
BANDS = ("high", "medium", "low")
OPEN_STATUSES = ("new", "reviewing", "confirmed")
LISTED_STATUSES = ("new", "reviewing", "confirmed", "dismissed")  # shown in the lists; cleared and marked_official never are
USER_STATUSES = ("reviewing", "confirmed", "dismissed")
STATUS_ALIASES = {"keep_reviewing": "reviewing", "keep-reviewing": "reviewing", "reviewing": "reviewing", "confirm": "confirmed",
                  "confirmed": "confirmed", "dismiss": "dismissed", "dismissed": "dismissed"}
SORTS = ("score_desc", "score_asc", "found_desc", "found_asc", "last_seen_desc")


class ActionError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class Filters:
    brand_id: int
    category: str | None = None
    type: str | None = None
    platform: str | None = None
    band: str | None = None
    severity: str | None = None
    resolution: str | None = None
    statuses: list[str] | None = None
    source: str | None = None  # live | sample | None = all
    q: str | None = None
    min_score: int | None = None
    from_: datetime | None = None
    to: datetime | None = None


def normalize_type(value: str | None) -> tuple[str | None, str | None]:
    """The `type` query accepts a detection type (fake_app ...) or a category word (social | app | apps)."""
    if not value:
        return None, None
    v = value.strip().lower()
    if v in ALL_TYPES:
        return v, None
    if v in ("social",):
        return None, "social"
    if v in ("app", "apps"):
        return None, "apps"
    if v in ("web", "website", "websites"):
        return None, "web"
    raise ActionError(422, f"type must be one of: {', '.join(ALL_TYPES)}, social, app, web")


def _base(f: Filters, *, ignore_type: bool = False, ignore_platform: bool = False) -> Select:
    q = select(Detection).where(Detection.brand_id == f.brand_id)
    statuses = f.statuses if f.statuses else list(LISTED_STATUSES)
    statuses = [s for s in statuses if s != "marked_official"]  # official assets never appear in a detection list
    q = q.where(Detection.status.in_(statuses or ["__none__"]))
    if f.category:
        q = q.where(Detection.category == f.category)
    if f.type and not ignore_type:
        q = q.where(Detection.type == f.type)
    if f.platform and not ignore_platform:
        q = q.where(Detection.platform == f.platform)
    if f.band:
        q = q.where(Detection.band == f.band)
    if f.resolution:
        q = q.where(Detection.resolution == f.resolution)
    if f.severity:
        q = q.where(Detection.severity == f.severity)
    if f.source:
        q = q.where(Detection.source == f.source)
    if f.min_score is not None:
        q = q.where(Detection.score >= f.min_score)
    if f.from_:
        q = q.where(Detection.found_at >= f.from_)
    if f.to:
        q = q.where(Detection.found_at <= f.to)
    if f.q and f.q.strip():
        t = f.q.strip()
        q = q.where(
            Detection.name.icontains(t, autoescape=True) | Detection.handle.icontains(t, autoescape=True)
            | Detection.package_id.icontains(t, autoescape=True) | Detection.publisher.icontains(t, autoescape=True)
        )
    return q


def _count_by(db: Session, q: Select, col) -> dict[str, int]:
    sub = q.subquery()
    rows = db.execute(select(getattr(sub.c, col.key), func.count()).group_by(getattr(sub.c, col.key))).all()
    return {k: n for k, n in rows}


def list_detections(db: Session, f: Filters, sort: str, limit: int, offset: int) -> dict[str, Any]:
    q = _base(f)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    order = {
        "score_desc": (Detection.score.desc(), Detection.id.desc()),
        "score_asc": (Detection.score.asc(), Detection.id.desc()),
        "found_desc": (Detection.found_at.desc(), Detection.id.desc()),
        "found_asc": (Detection.found_at.asc(), Detection.id.asc()),
        "last_seen_desc": (Detection.last_seen_at.desc(), Detection.id.desc()),
    }[sort]
    rows = list(db.scalars(q.order_by(*order).limit(limit).offset(offset)))
    # tab counts: same filters except the tab being counted (type / platform), so "All 6 / Fake profiles 3" add up
    no_tab = _base(f, ignore_type=True, ignore_platform=True)
    by_type = _count_by(db, no_tab, Detection.type)
    by_platform = _count_by(db, _base(f, ignore_platform=True), Detection.platform)
    by_status = _count_by(db, _base(Filters(**{**f.__dict__, "statuses": None})), Detection.status)
    return {
        "total": total,
        "rows": rows,
        "counts": {
            "all": db.scalar(select(func.count()).select_from(no_tab.subquery())) or 0,
            "by_type": {t: by_type.get(t, 0) for t in ALL_TYPES},
            "by_category": _count_by(db, _base(Filters(**{**f.__dict__, "category": None, "type": None, "platform": None})), Detection.category),
            "by_severity": _count_by(db, _base(f), Detection.severity),
            "by_platform": dict(sorted(by_platform.items())),
            "by_status": by_status,
            "by_band": _count_by(db, _base(f), Detection.band),
        },
    }


# ---------------------------------------------------------------------------------------------------------
# serialization


def _iso(d: datetime | None) -> str | None:
    return iso_z(d) if d else None


def _handle_out(d: Detection) -> str | None:
    return display_handle(d.platform, d.handle) if d.handle else None


def summary_row(d: Detection) -> dict:
    det = d.details or {}
    return {
        "id": d.id,
        "category": d.category,
        "type": d.type,
        "platform": d.platform,
        "name": d.name,
        "handle": _handle_out(d),
        "package_id": d.package_id,
        "publisher": d.publisher,
        "profile_url": d.profile_url,
        "score": d.score,
        "risk_band": d.band,
        "severity": d.severity,
        "status": d.status,
        "resolution": d.resolution,
        "summary": det.get("summary", ""),
        "tags": det.get("tags", []),
        "found_at": _iso(d.found_at),
        "last_seen_at": _iso(d.last_seen_at),
        "stale": d.stale,
        "source": d.source,
        "origin": d.origin,
    }


def _official_side(brand: Brand, d: Detection) -> dict:
    if d.category == "web":
        return {"name": brand.name, "domains": list(brand.domains or []), "on_allowlist": False}
    if d.category == "apps":
        apps = list(brand.apps)
        match = next((a for a in apps if d.platform in (a.stores or [])), apps[0] if apps else None)
        pubs = sorted({a.publisher for a in apps if a.publisher})
        return {
            "name": match.name if match else brand.name, "package_id": match.package_id if match else None,
            "publisher": match.publisher if match else None, "stores": match.stores if match else [], "downloads": None,
            "on_allowlist": match is not None, "official_publishers": pubs,
        }
    acc = next((a for a in brand.accounts if a.platform == d.platform), None)
    return {
        "name": brand.name, "handle": display_handle(acc.platform, acc.handle) if acc else None, "url": acc.url if acc else None,
        "bio": None, "followers": None, "joined": None, "link": (brand.domains[0] if brand.domains else None), "on_allowlist": acc is not None,
    }


def _logo_pct(d: Detection) -> float | None:
    logo = (d.details or {}).get("logo") or {}
    return logo.get("similarity") if logo.get("status") == "compared" else None


def detail(db: Session, d: Detection, brand: Brand) -> dict:
    det = d.details or {}
    cand = det.get("candidate") or {}
    ev = d.evidence or {}
    events = list(db.scalars(select(DetectionEvent).where(DetectionEvent.detection_id == d.id).order_by(DetectionEvent.id)))
    out = summary_row(d)
    out.update({
        "brand_id": d.brand_id,
        "first_seen_at": _iso(d.first_seen_at),
        "algorithm_version": d.algorithm_version,
        "scored_at": _iso(d.scored_at),
        "weights": det.get("weights"),
        "signals": det.get("signals") or [],
        "domain": det.get("domain"),
        "reputation": det.get("reputation"),
        "name_match": det.get("name_match"),
        "matched_text": det.get("matched_text"),
        "raw_total": det.get("raw_total"),
        "capped": det.get("capped"),
        "threshold": det.get("threshold"),
        "reasons": d.reasons or [],
        "official": _official_side(brand, d),
        "logo": det.get("logo") or {"status": "unavailable", "similarity": None, "reason": "Logo unavailable"},
        "evidence": {
            "page_url": ev.get("page_url"), "captured_at": ev.get("captured_at"), "sha256": ev.get("sha256"),
            "snapshot_url": f"/api/detections/{d.id}/evidence", "screenshot_url": f"/api/detections/{d.id}/evidence/screenshot" if ev.get("screenshot_path") else None,
            "screenshot_sha256": ev.get("screenshot_sha256"), "history": ev.get("history", []),
        },
        "score_history": d.score_history or [],
        "notes": d.notes or [],
        "history": [
            {"at": _iso(e.at), "actor": e.actor, "kind": e.kind, "from": e.from_status, "to": e.to_status, "note": e.note} for e in events
        ],
        "extras": det.get("extras") or {},
        "text_analysis": det.get("text_analysis"),
    })
    if d.category == "apps":
        pubs = out["official"].get("official_publishers") or []
        out.update({
            "publisher_differs": bool(d.publisher) and d.publisher.strip().lower() not in {p.lower() for p in pubs},
            "official_publisher": pubs[0] if pubs else None,
            "suspect": {"stores": [d.platform], "downloads": None, "released": (det.get("extras") or {}).get("released_at"),
                        "icon_match_percent": _logo_pct(d), "rating": cand.get("rating"), "rating_count": cand.get("rating_count")},
            "description": {"snippet": (cand.get("bio") or "")[:400] or None,
                            "overlap_points": next((r["points"] for r in (d.reasons or []) if r["signal"] == "description_overlap"), 0)},
        })
    elif d.category == "web":
        dom = det.get("domain") or {}
        out["suspect"] = {"url": d.profile_url, "host": d.name, "registrable_domain": (dom.get("url") or {}).get("registrable_domain"),
                          "components": dom.get("components"), "reputation": det.get("reputation")}
    else:
        out["suspect"] = {"bio": cand.get("bio"), "followers": cand.get("followers"), "following": cand.get("following"),
                          "posts": cand.get("posts"), "joined": cand.get("created_at"), "verified": cand.get("verified"),
                          "link": d.profile_url, "logo_match_percent": _logo_pct(d)}
    return out


def evidence_bundle(d: Detection, data_dir: Path) -> dict:
    ev = d.evidence or {}
    snap, ok = evidence.read_snapshot(data_dir, ev.get("snapshot_path"), ev.get("sha256"))
    return {
        "page_url": ev.get("page_url"), "captured_at": ev.get("captured_at"), "sha256": ev.get("sha256"),
        "hash_verified": ok, "snapshot": snap, "screenshot_available": bool(ev.get("screenshot_path")),
        "screenshot_sha256": ev.get("screenshot_sha256"), "history": ev.get("history", []),
    }


# ---------------------------------------------------------------------------------------------------------
# actions


def _event(db: Session, d: Detection, user: User, *, frm: str | None, to: str | None, note: str, kind: str = "status") -> None:
    db.add(DetectionEvent(detection_id=d.id, at=utcnow(), actor=user.name, actor_id=user.id, kind=kind, from_status=frm, to_status=to, note=note))


def _target(d: Detection) -> str:
    return (_handle_out(d) or d.package_id or d.name)[:300]


def change_status(db: Session, d: Detection, user: User, to: str, note: str = "") -> tuple[Detection, int | None]:
    """A person records a decision. `new`/`cleared`/`marked_official` cannot be set here."""
    to = STATUS_ALIASES.get(to, to)
    if to not in USER_STATUSES:
        raise ActionError(422, "status must be one of: reviewing, confirmed, dismissed")
    if d.status == "marked_official":
        raise ActionError(409, "This asset was marked official and is on the allowlist. Remove it from the allowlist first.")
    old = d.status
    note = (note or "").strip()
    if old == to and not note:
        return d, None
    d.status = to
    d.updated_at = utcnow()
    if note:
        d.notes = [*(d.notes or []), {"at": iso_z(utcnow()), "actor": user.name, "text": note, "kind": "reviewer"}][-50:]
    _event(db, d, user, frm=old, to=to, note=note)
    row = audit.record(db, user, "detection.status_changed", _target(d), f"{old} -> {to}" + (f": {note}" if note else ""), source=d.source)
    db.commit()
    return d, row.id


RESOLUTIONS = ("true_positive", "false_positive", "resolved", "monitoring")


def set_resolution(db: Session, d: Detection, user: User, resolution: str, note: str = "") -> tuple[Detection, int]:
    """Records the investigation outcome. Audited and added to the detection's event log; the status is left alone."""
    if resolution not in RESOLUTIONS:
        raise ActionError(422, "resolution must be one of: " + ", ".join(RESOLUTIONS))
    old = d.resolution
    note = (note or "").strip()
    d.resolution = resolution
    d.updated_at = utcnow()
    if note:
        d.notes = [*(d.notes or []), {"at": iso_z(utcnow()), "actor": user.name, "text": note, "kind": "reviewer"}][-50:]
    _event(db, d, user, frm=old, to=resolution, note=note, kind="resolution")
    row = audit.record(db, user, "detection.resolution_set", _target(d), f"{old or 'none'} -> {resolution}" + (f": {note}" if note else ""), source=d.source)
    db.commit()
    return d, row.id


def mark_official(db: Session, brand: Brand, d: Detection, user: User, note: str = "", publisher: str | None = None) -> dict:
    """Adds the asset to the allowlist (official_accounts / official_apps), records an official_matches row and
    closes the detection. Future scans drop it BEFORE scoring."""
    if d.status == "marked_official":
        raise ActionError(409, "This detection is already marked official.")
    now = utcnow()
    audit_actions: list[tuple[str, str, str]] = []
    if d.category == "web":
        raise ActionError(422, "A look-alike domain cannot be marked official here. If it really is yours, add its domain to the brand's official domains on the Brand Profile.")
    if d.category == "apps":
        if d.platform not in ("google_play", "app_store"):
            raise ActionError(422, "Only apps listed on Google Play or the App Store can be marked official. A third-party APK listing is never official.")
        pkg = (d.package_id or "").strip()
        if not pkg:
            raise ActionError(422, "This detection has no package id.")
        entry = db.scalar(select(OfficialApp).where(OfficialApp.brand_id == brand.id, OfficialApp.package_key == pkg.lower()))
        if entry is None:
            pub = (publisher or d.publisher or brand.name).strip()[:120]
            entry = OfficialApp(brand_id=brand.id, name=(d.name or pkg)[:120], package_id=pkg[:155], package_key=pkg.lower()[:155],
                                publisher=pub or brand.name[:120], stores=[d.platform], status="verified", source=d.source)
            db.add(entry)
            audit_actions.append(("app.added", f"{entry.name} ({entry.package_id})", f"marked official from detection {d.id}"))
        elif d.platform not in (entry.stores or []):
            entry.stores = [*(entry.stores or []), d.platform]
        db.flush()
        kind, label, rule_id = "app", f"{d.name} · {pkg}", {"official_app_id": entry.id}
        out_entry = {"kind": "app", "id": entry.id}
    else:
        handle = (d.handle or "").lstrip("@")
        if not handle:
            raise ActionError(422, "This detection has no handle.")
        if len(handle) > 30:
            raise ActionError(422, "The handle is longer than 30 characters, which the allowlist cannot store yet.")
        try:
            url = build_account_url(d.platform, handle)
        except KeyError:
            url = (d.profile_url or "").removeprefix("https://").removeprefix("http://")
        entry = db.scalar(select(OfficialAccount).where(OfficialAccount.brand_id == brand.id, OfficialAccount.platform == d.platform,
                                                         OfficialAccount.handle_key == handle.lower()))
        if entry is None:
            entry = OfficialAccount(brand_id=brand.id, platform=d.platform, handle=handle, handle_key=handle.lower(), url=url[:300] or handle,
                                    status="verified", check_message="Marked official by a reviewer", last_checked_at=now, source=d.source)
            db.add(entry)
            audit_actions.append(("account.added", f"{display_handle(d.platform, handle)} on {d.platform}", f"marked official from detection {d.id}"))
        db.flush()
        kind, label, rule_id = "account", f"{display_handle(d.platform, handle)} on {d.platform}", {"official_account_id": entry.id}
        out_entry = {"kind": "account", "id": entry.id}
    key = f"{kind}:{d.platform}:{(d.package_id if kind == 'app' else d.handle or '').lstrip('@').lower()}"[:400]
    match = db.scalar(select(OfficialMatch).where(OfficialMatch.brand_id == brand.id, OfficialMatch.match_key == key))
    rule = f"Marked official by {user.name}" + (f": {note.strip()}" if note and note.strip() else "")
    if match is None:
        db.add(OfficialMatch(brand_id=brand.id, scan_id=d.last_scan_id, kind=kind, platform=d.platform, label=label[:300], reason="marked_official",
                             matched_rule=rule[:300], match_key=key, url=d.profile_url, last_seen_at=now, times_seen=1, source=d.source, **rule_id))
    else:
        match.reason, match.matched_rule, match.last_seen_at = "marked_official", rule[:300], now
    old = d.status
    d.status, d.updated_at = "marked_official", now
    if note and note.strip():
        d.notes = [*(d.notes or []), {"at": iso_z(now), "actor": user.name, "text": note.strip(), "kind": "reviewer"}][-50:]
    _event(db, d, user, frm=old, to="marked_official", note=note or "Added to the allowlist")
    main_row = audit.record(db, user, "detection.marked_official", _target(d), f"{old} -> marked_official" + (f": {note.strip()}" if note and note.strip() else ""), source=d.source)
    for action, target, detail in audit_actions:
        audit.record(db, user, action, target, detail, source=d.source)
    brand.updated_at = now
    db.commit()
    official_assets = len(brand.accounts) + len(brand.apps)
    return {"allowlist_entry": out_entry, "official_assets": official_assets, "audit_id": main_row.id}


# ---------------------------------------------------------------------------------------------------------
# keep detection lists consistent with the allowlist


def candidate_from_detection(d: Detection) -> Candidate:
    cand = (d.details or {}).get("candidate") or {}
    return Candidate(
        platform=d.platform, kind={"apps": "app", "web": "web"}.get(d.category, "social"), handle=(d.package_id if d.category == "apps" else d.handle),
        display_name=d.name, profile_url=d.profile_url, source=d.source, store=cand.get("store"), publisher=d.publisher,
        extras=(d.details or {}).get("extras") or {},
    )


def sync_with_allowlist(db: Session, brand: Brand, user: User | None) -> int:
    """After an official account/app is added, any detection for exactly that asset becomes `marked_official`
    (so it leaves every detection list). Returns how many were closed."""
    allow = BrandAllowlist(brand)
    closed = 0
    for d in db.scalars(select(Detection).where(Detection.brand_id == brand.id, Detection.status != "marked_official")):
        hit = allow.check(candidate_from_detection(d))
        if hit is None:
            continue
        old = d.status
        d.status = "marked_official"
        db.add(DetectionEvent(detection_id=d.id, at=utcnow(), actor=user.name if user else "system", actor_id=user.id if user else None,
                              kind="status", from_status=old, to_status="marked_official", note=f"Added to the allowlist ({hit.rule})"))
        audit.record(db, user, "detection.marked_official", _target(d), f"{old} -> marked_official (matches new allowlist entry)", source=d.source)
        closed += 1
    return closed


# ---------------------------------------------------------------------------------------------------------
# excluded-as-official and false-positive guard lists


def excluded_items(db: Session, brand: Brand, category: str | None, source: str | None) -> list[dict]:
    matches = list(db.scalars(select(OfficialMatch).where(OfficialMatch.brand_id == brand.id)))
    out: list[dict] = []
    if category in (None, "social"):
        for a in brand.accounts:
            ms = [m for m in matches if m.official_account_id == a.id]
            out.append({
                "kind": "account", "id": a.id, "platform": a.platform, "label": f"{display_handle(a.platform, a.handle)} on {a.platform}",
                "status": "Official · not flagged", "reason": "exact_handle", "matched_rule": f"Exact official {a.platform} handle.",
                "times_seen": sum(m.times_seen or 0 for m in ms), "last_seen_at": _iso(max((m.last_seen_at for m in ms if m.last_seen_at), default=None)),
                "source": a.source,
            })
    if category in (None, "apps"):
        for a in brand.apps:
            ms = [m for m in matches if m.official_app_id == a.id]
            out.append({
                "kind": "app", "id": a.id, "platform": None, "label": f"{a.name} · {a.package_id}",
                "sub": f"{a.publisher} · {', '.join(a.stores or [])}", "status": "Official · not flagged", "reason": "package_id",
                "matched_rule": "Exact official package id.", "times_seen": sum(m.times_seen or 0 for m in ms),
                "last_seen_at": _iso(max((m.last_seen_at for m in ms if m.last_seen_at), default=None)), "source": a.source,
            })
    if source:
        out = [i for i in out if i["source"] == source]
    return out


def false_positive_items(db: Session, brand: Brand, category: str | None, source: str | None) -> list[dict]:
    q = select(Detection).where(Detection.brand_id == brand.id, Detection.status == "cleared")
    if category:
        q = q.where(Detection.category == category)
    if source:
        q = q.where(Detection.source == source)
    items = []
    for d in db.scalars(q.order_by(Detection.score.desc(), Detection.id.desc())):
        det = d.details or {}
        logo = det.get("logo") or {}
        pct = logo.get("similarity") if logo.get("status") == "compared" else None
        items.append({
            "id": d.id, "name": d.name, "platform": d.platform, "category": d.category, "handle": _handle_out(d), "package_id": d.package_id,
            "publisher": d.publisher, "score": d.score, "name_match_percent": (det.get("name_match") or {}).get("pct"),
            "logo_match_percent": pct if d.category == "social" else None, "icon_match_percent": pct if d.category == "apps" else None,
            "logo_note": None if pct is not None else logo.get("reason"), "reason": det.get("clear_reason", ""),
            "status": "not_flagged", "last_seen_at": _iso(d.last_seen_at), "source": d.source,
        })
    return items

"""Dashboard aggregates. Every number is computed from stored rows: nothing is hard-coded."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.time import as_utc
from app.models import Brand, Detection
from app.services.detections import ALL_TYPES, OPEN_STATUSES, TYPES, WEB_TYPES, summary_row


def _percentages(counts: dict[str, int]) -> dict[str, int]:
    """Whole-number percentages that add up to 100 (largest remainder)."""
    total = sum(counts.values())
    if total == 0:
        return {k: 0 for k in counts}
    raw = {k: v * 100 / total for k, v in counts.items()}
    out = {k: int(r) for k, r in raw.items()}
    for k in sorted(counts, key=lambda k: raw[k] - out[k], reverse=True)[: 100 - sum(out.values())]:
        out[k] += 1
    return out


def _group(db: Session, base, col) -> dict[str, int]:
    sub = base.subquery()
    return {k: n for k, n in db.execute(select(getattr(sub.c, col.key), func.count()).group_by(getattr(sub.c, col.key))).all()}


def kpi(db: Session, base, now: datetime, days: int = 7) -> dict:
    value = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    since = now - timedelta(days=days)
    delta = db.scalar(select(func.count()).select_from(base.where(Detection.found_at >= since).subquery())) or 0
    trend = []
    for i in range(days - 1, -1, -1):
        end = (now - timedelta(days=i)).replace(hour=23, minute=59, second=59, microsecond=0)
        trend.append(db.scalar(select(func.count()).select_from(base.where(Detection.found_at <= end).subquery())) or 0)
    return {"value": value, "delta_week": delta, "trend": trend}


def build(db: Session, brand: Brand, mode: str, now: datetime) -> dict:
    now = as_utc(now)
    scoped = select(Detection).where(Detection.brand_id == brand.id, Detection.source == mode)
    open_q = scoped.where(Detection.status.in_(OPEN_STATUSES))
    visible = scoped.where(Detection.status.in_((*OPEN_STATUSES, "dismissed")))

    by_type = _group(db, open_q, Detection.type)
    type_counts = {t: by_type.get(t, 0) for t in ALL_TYPES}
    pct = _percentages(type_counts)
    by_platform = sorted(_group(db, open_q, Detection.platform).items(), key=lambda kv: (-kv[1], kv[0]))
    by_status = _group(db, visible, Detection.status)
    by_band = _group(db, open_q, Detection.band)
    by_severity = _group(db, open_q, Detection.severity)
    needs = db.scalars(scoped.where(Detection.status.in_(("new", "reviewing"))).order_by(Detection.score.desc(), Detection.id.desc()).limit(5)).all()
    recent = db.scalars(open_q.order_by(Detection.found_at.desc(), Detection.id.desc()).limit(6)).all()
    top = db.scalars(open_q.order_by(Detection.score.desc(), Detection.id.desc()).limit(1)).first()
    total_open = sum(type_counts.values())
    sample_rows = db.scalar(select(func.count()).select_from(visible.where(Detection.source == "sample").subquery())) or 0
    return {
        "brand": {"id": brand.id, "name": brand.name},
        "data_mode": mode,
        "is_sample": mode == "sample",
        "includes_sample": sample_rows > 0,
        "kpis": {
            "total_detections": kpi(db, open_q, now),
            "social_impersonations": kpi(db, open_q.where(Detection.category == "social"), now),
            "suspicious_apps": kpi(db, open_q.where(Detection.category == "apps"), now),
            "web_threats": kpi(db, open_q.where(Detection.category == "web"), now),
            "official_assets": {"value": len(brand.accounts) + len(brand.apps)},
        },
        "counts": {
            "by_status": {s: by_status.get(s, 0) for s in ("new", "reviewing", "confirmed", "dismissed")},
            "by_band": {b: by_band.get(b, 0) for b in ("high", "medium", "low")},
            "by_severity": {sv: by_severity.get(sv, 0) for sv in ("LOW", "MEDIUM", "HIGH", "CRITICAL")},
            "by_type": type_counts,
            "by_category": {
                "social": sum(n for t, n in type_counts.items() if t in TYPES and t != "fake_app"),
                "apps": type_counts["fake_app"],
                "web": sum(type_counts[t] for t in WEB_TYPES),
            },
            "cleared": db.scalar(select(func.count()).select_from(select(Detection).where(
                Detection.brand_id == brand.id, Detection.source == mode, Detection.status == "cleared").subquery())) or 0,
        },
        # the web type is listed only once something was found, so a workspace without web findings looks exactly as before
        "by_type": [{"type": t, "count": type_counts[t], "percent": pct[t]}
                    for t in sorted(ALL_TYPES, key=lambda t: (-type_counts[t], t)) if t in TYPES or type_counts[t] > 0],
        "by_platform": [{"platform": p, "count": n} for p, n in by_platform],
        "total_open": total_open,
        "needs_review": [
            {"id": d.id, "name": d.handle and summary_row(d)["handle"] or d.name, "platform": d.platform, "type": d.type,
             "found_at": summary_row(d)["found_at"], "risk_band": d.band, "score": d.score}
            for d in needs
        ],
        "recent": [
            {"id": d.id, "name": summary_row(d)["handle"] or d.name, "summary": (d.details or {}).get("summary", ""), "platform": d.platform,
             "type": d.type, "score": d.score, "status": d.status, "found_at": summary_row(d)["found_at"], "source": d.source}
            for d in recent
        ],
        "top_detection": None if top is None else {
            "id": top.id, "name": summary_row(top)["handle"] or top.name, "platform": top.platform, "score": top.score, "risk_band": top.band,
            "on_allowlist": False, "tags": (top.details or {}).get("tags", []), "source": top.source,
        },
        "why_flagged": None if top is None else {
            "detection_id": top.id, "score": top.score,
            "reasons": [{"signal": r["signal"], "label": r.get("label"), "detail": r.get("detail"), "points": r["points"]} for r in (top.reasons or [])],
        },
    }

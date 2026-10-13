import threading
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import mode_of, resolve_brand
from app.config.deps import get_current_user, require_writer
from app.config.time import iso_z
from app.core.engine.evaluation import BRANDS, confusion, evaluate_brand, merge
from app.core.engine.name_match import TECHNIQUES
from app.core.engine.risk_engine import ALGORITHM_V1, ALGORITHM_V2
from app.core.engine.synthetic import MIXED
from app.core.pipeline import lookalike
from app.db.session import get_db
from app.models import Detection, User
from app.services import audit

router = APIRouter(prefix="/evaluation")

DATASET_NAME = "scammind-synthetic-names"
DATASET_VERSION = "2"  # v2 added fan pages and authorized resellers as hard negatives

LABEL = (
    "Synthetic numbers are optimistic. The test names are produced by the same set of tricks the matcher was built to catch, "
    "so they show the matcher is consistent, not how it performs on real impersonators. Real precision comes only from "
    "reviewer decisions (confirmed vs dismissed), shown under 'detection'."
)
_cache: dict[tuple[int, int], dict] = {}
_lock = threading.Lock()


def synthetic_evaluation(seed: int = 7, per_technique: int = 25, refresh: bool = False) -> dict:
    """Run the synthetic generator over the built-in brands. Cached in memory: the inputs are fixed, so the result is stable."""
    key = (seed, per_technique)
    with _lock:
        if not refresh and key in _cache:
            return {**_cache[key], "cached": True}
        results = [evaluate_brand(b, seed, per_technique) for b in BRANDS]
        combined = merge(results)
        out = {
            "seed": seed, "per_technique": per_technique, "brands": len(BRANDS), "computed_at": iso_z(datetime.now(UTC)),
            "by_technique": [{"technique": t, **combined[t]} for t in (*TECHNIQUES, MIXED)],
            "overall": combined["overall"],
            "confusion": confusion(results),
            "dataset": {"name": DATASET_NAME, "version": DATASET_VERSION, "size": confusion(results)["total_cases"], "synthetic": True,
                        "brands": list(BRANDS), "negative_groups": ["hard_negative", "fan_page", "authorized_reseller", "unrelated"],
                        "split": "none: every case is a test case, nothing is trained, so no variants can leak between splits",
                        "banner": "SYNTHETIC DATA: generated, not real impersonation reports."},
            "algorithm_version": {"social_app": ALGORITHM_V1, "web": ALGORITHM_V2},
            "ml_classifier": "deferred until labelled analyst data exists",
            "false_positives": sorted({fp for r in results for fp in r["false_positives"]})[:20],
        }
        _cache[key] = out
        return {**out, "cached": False}


def build(db: Session, brand, mode: str, refresh: bool) -> dict:
    cases = lookalike.run_cases(db, brand)
    rows = db.scalars(select(Detection).where(Detection.brand_id == brand.id, Detection.source == mode)).all()
    confirmed = sum(d.status == "confirmed" for d in rows)
    dismissed = sum(d.status == "dismissed" for d in rows)
    by_type: dict[str, dict] = {}
    for d in rows:
        if d.status in ("confirmed", "dismissed"):
            b = by_type.setdefault(d.type, {"type": d.type, "confirmed": 0, "dismissed": 0})
            b[d.status] += 1
    for b in by_type.values():
        n = b["confirmed"] + b["dismissed"]
        b["precision"] = round(b["confirmed"] / n, 3) if n else None
    by_cat: dict[str, dict] = {c: {"category": c, "total": 0, "confirmed": 0, "dismissed": 0} for c in ("social", "apps", "web")}
    for d in rows:
        c = by_cat.setdefault(d.category, {"category": d.category, "total": 0, "confirmed": 0, "dismissed": 0})
        c["total"] += 1
        if d.status in ("confirmed", "dismissed"):
            c[d.status] += 1
    for c in by_cat.values():
        n = c["confirmed"] + c["dismissed"]
        c["precision"] = round(c["confirmed"] / n, 3) if n else None
    return {
        "by_category": list(by_cat.values()),
        "data_mode": mode, "is_sample": mode == "sample", "label": LABEL, "synthetic": synthetic_evaluation(refresh=refresh),
        "lookalike_tests": {**{k: cases[k] for k in ("total", "passed", "failed", "summary", "ran_at")}, **lookalike.confusion(cases["items"]), "items": cases["items"]},
        "reviewed": {"confirmed": confirmed, "dismissed": dismissed, "marked_official": sum(d.status == "marked_official" for d in rows),
                     "pending": sum(d.status in ("new", "reviewing") for d in rows)},
        "detection": {"true_positives": confirmed, "false_positives": dismissed,
                      "precision": round(confirmed / (confirmed + dismissed), 3) if confirmed + dismissed else None,
                      "definition": "precision = confirmed / (confirmed + dismissed), from reviewer decisions. Recall cannot be measured without labelled ground truth."},
        "by_type": sorted(by_type.values(), key=lambda b: b["type"]),
    }


@router.get("")
def get_evaluation(
    request: Request, brand_id: int | None = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    return build(db, resolve_brand(db, brand_id), mode_of(db, request), refresh=False)


@router.post("/run")
def run_evaluation(request: Request, brand_id: int | None = None, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> dict:
    """Re-runs the synthetic evaluation (cache refreshed) and the labelled look-alike cases."""
    brand = resolve_brand(db, brand_id)
    out = build(db, brand, mode_of(db, request), refresh=True)
    audit.record(db, user, "evaluation.run", brand.name, f"look-alike cases: {out['lookalike_tests']['summary']}")
    db.commit()
    return out

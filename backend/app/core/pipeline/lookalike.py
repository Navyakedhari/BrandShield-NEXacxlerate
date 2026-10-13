"""Look-alike Lab: runs the live matcher on a typed name against the brand, and the labelled test-case runner.

Same engine and same Settings (rules, impersonation words) as a real scan. Pure and side-effect free except for
the stored test cases. In a real scan the name alone never decides: logo, bio, publisher and account signals are
scored too, and only an exact official handle / URL / package id excludes a candidate.
"""

from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.time import iso_z, utcnow
from app.core.engine.name_match import BRAND_NAME, FLAGGED, TECHNIQUE_LABELS
from app.core.engine.normalize import LEET_DIGITS, fold_text, prepare
from app.core.pipeline.matching import RULE_LABELS, TECHNIQUE_RULE, BrandAllowlist, apply_rules, build_matcher
from app.models import Brand, LookalikeCase
from app.services import app_settings

VERDICTS = ("flagged", "not_flagged", "official")

# 12 labelled cases (7 look-alikes, 3 other businesses, 2 official). Written for the fictional brand "Acme Bank".
DEFAULT_CASES: list[tuple[str, str]] = [
    ("acrnebank", "flagged"),
    ("Acme Bank Support", "flagged"),
    ("acme-bank", "flagged"),
    ("A C M E Bank", "flagged"),
    ("acm3bank", "flagged"),
    ("Acme Bnak", "flagged"),
    ("acmebank_official", "flagged"),
    ("Acme Bakery", "not_flagged"),
    ("Acme Hotels", "not_flagged"),
    ("Acme Weather", "not_flagged"),
    ("Acme Bank", "official"),
    ("@acmebank", "official"),
]


_SEED_LOCK = threading.Lock()


def ensure_default_cases(db: Session) -> None:
    """Seed the 12 default cases once (only when the table is empty, so an admin's deletions are respected).

    Two requests can arrive together (a page that loads the list twice, React StrictMode in development). Without a lock
    both would see an empty table and insert 12 rows each (24 cases). The lock serialises the check-then-insert inside this
    process; across several API processes the partial unique index from migration 0004 makes the loser's insert fail and be ignored.
    """
    if db.scalar(select(LookalikeCase.id).limit(1)) is not None:
        return
    with _SEED_LOCK:
        if db.scalar(select(LookalikeCase.id).limit(1)) is not None:  # another request seeded while we waited
            return
        for name, expected in DEFAULT_CASES:
            db.add(LookalikeCase(name=name, expected=expected, builtin=True, source="sample"))
        try:
            db.commit()
        except IntegrityError:
            # Another process seeded first (partial unique index uq_lookalike_cases_builtin_name, migration 0004).
            db.rollback()


def _normalization(name: str, brand: Brand, added: list[str]) -> list[dict]:
    text, items = prepare(name)
    leet_folded = "".join(LEET_DIGITS.get(i.ch, i.ch) if i.kind == "alnum" else i.ch for i in items)
    compact_brand = "".join(brand.name.lower().split())
    return [
        {"step": 1, "label": "You typed", "value": name},
        {"step": 2, "label": "Lower-cased", "value": text.lower()},
        {"step": 3, "label": "Look-alike characters folded", "value": leet_folded},
        {"step": 4, "label": "Spaces and symbols removed", "value": fold_text(name, digraph=True)},
        {"step": 5, "label": "Brand and extra words", "value": f"{compact_brand} + {' '.join(added)}" if added else compact_brand},
    ]


def evaluate_name(db: Session, brand: Brand, name: str, platform: str | None = None) -> dict:
    name = " ".join(name.split())
    mon = app_settings.get_monitoring(db)
    matcher = build_matcher(brand, words=mon["impersonation_words"])
    allow = BrandAllowlist(brand)
    bare = name.lstrip("@").lower()
    handles = allow.assets.social_handles
    on_list = any(bare in {h.lower() for h in hs} for p, hs in handles.items() if platform in (None, p))
    on_list = on_list or bare in {p.lower() for p in allow.assets.play_package_ids}
    r = matcher.match(name)
    r = apply_rules(r, mon["rules"])
    base = {
        "input": name, "brand": brand.name, "match_percent": r.name_match_pct, "engine_verdict": r.verdict, "techniques": r.techniques,
        "technique_labels": [TECHNIQUE_LABELS.get(t, t) for t in r.techniques], "squat_category": r.squat_category,
        "edit_distance": r.edit_distance, "normalization": _normalization(name, brand, r.added_words), "on_allowlist": on_list,
    }
    if on_list:
        return {**base, "verdict": "official", "official_basis": "allowlist",
                "headline": f"Official: matches an allowlisted handle of {brand.name}",
                "explanation": "This is an exact official handle or package id, so a scan skips it before scoring.", "reasons": []}
    if r.verdict == BRAND_NAME:
        return {**base, "verdict": "official", "official_basis": "name",
                "headline": f"Official name: this is the exact name of {brand.name}",
                "explanation": "It uses the exact brand name. In a real scan the name alone never excludes anything: only an exact official "
                               "handle, URL or package id on your allowlist does. Anything else with this name goes to review.",
                "reasons": []}
    if r.verdict == FLAGGED:
        reasons = [{"rule": TECHNIQUE_RULE.get(t, t), "label": TECHNIQUE_LABELS.get(t, t).capitalize()} for t in r.techniques]
        if r.impersonation_words:
            reasons.append({"rule": "impersonation_words", "label": f"Impersonation word: {', '.join(r.impersonation_words)}"})
        return {**base, "verdict": "flagged", "official_basis": None, "headline": f"Flagged as a look-alike of {brand.name}",
                "explanation": "This name matches the brand after normalization and is not on your allowlist. It goes to the review queue "
                               "with these reasons. " + " ".join(r.notes),
                "reasons": reasons}
    why = " ".join(r.notes) or "The name does not resemble the brand."
    disabled = [RULE_LABELS[k] for k, on in mon["rules"].items() if not on]
    extra = f" Switched-off rules: {', '.join(disabled)}." if disabled and r.verdict == "review" else ""
    return {**base, "verdict": "not_flagged", "official_basis": None, "headline": f"Not flagged: {name} is not a look-alike of {brand.name}",
            "explanation": why + extra, "reasons": []}


def run_cases(db: Session, brand: Brand) -> dict:
    """Run every stored case through `evaluate_name`. The result is computed, never copied from `expected`."""
    ensure_default_cases(db)
    cases = list(db.scalars(select(LookalikeCase).order_by(LookalikeCase.id)))
    items, passed = [], 0
    for c in cases:
        r = evaluate_name(db, brand, c.name)
        ok = r["verdict"] == c.expected
        passed += ok
        items.append({"id": c.id, "name": c.name, "expected": c.expected, "result": r["verdict"], "pass": ok, "match_percent": r["match_percent"],
                      "builtin": c.builtin})
    total = len(items)
    return {"total": total, "passed": passed, "failed": total - passed, "summary": f"{passed} of {total} pass", "items": items,
            "ran_at": iso_z(utcnow())}


def confusion(items: list[dict]) -> dict:
    """Precision / recall of 'flagged' over the labelled cases (official and not_flagged both count as 'not predicted flagged')."""
    tp = sum(i["expected"] == "flagged" and i["result"] == "flagged" for i in items)
    fn = sum(i["expected"] == "flagged" and i["result"] != "flagged" for i in items)
    fp = sum(i["expected"] != "flagged" and i["result"] == "flagged" for i in items)
    tn = sum(i["expected"] != "flagged" and i["result"] != "flagged" for i in items)
    return {
        "true_positives": tp, "false_negatives": fn, "false_positives": fp, "true_negatives": tn,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "flagged_expected": sum(i["expected"] == "flagged" for i in items),
        "not_flagged_expected": sum(i["expected"] == "not_flagged" for i in items),
        "official_expected": sum(i["expected"] == "official" for i in items),
    }

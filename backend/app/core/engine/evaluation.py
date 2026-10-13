"""Precision / recall of the name matcher over the synthetic generator (shared by scripts/evaluate.py and the API).

These numbers are OPTIMISTIC: the test names come from the same set of tricks the matcher was built to catch.
They are a regression check, not a measure of real-world accuracy.
"""

from __future__ import annotations

from collections import defaultdict

from app.core.engine.name_match import TECHNIQUES, NameMatcher
from app.core.engine.synthetic import MIXED, generate_cases

BRANDS = ["Acme Bank", "Zenith Pay", "Orbit Telecom", "Zoho", "Northern Lights Mutual Insurance"]


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3)}


def evaluate_brand(brand: str, seed: int, per_technique: int) -> dict:
    others = [b for b in BRANDS if b != brand]
    cases = generate_cases(brand, seed=seed, per_technique=per_technique, other_brands=others)
    matcher = NameMatcher(brand)
    tp = defaultdict(int)
    fn = defaultdict(int)
    label_ok = defaultdict(int)
    fp_neg: list[str] = []
    neg_groups: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # group -> [n, flagged]
    misses: dict[str, list[str]] = defaultdict(list)
    n_neg = 0
    for c in cases:
        r = matcher.match(c.text)
        if c.expect_flagged:
            if r.verdict == "flagged":
                tp[c.technique] += 1
                if (len(r.techniques) >= 2) if c.technique == MIXED else (c.technique in r.techniques):
                    label_ok[c.technique] += 1
            else:
                fn[c.technique] += 1
                misses[c.technique].append(f"{c.text!r} -> {r.verdict}")
        else:
            n_neg += 1
            neg_groups[c.group or "other"][0] += 1
            if r.verdict == "flagged":
                neg_groups[c.group or "other"][1] += 1
                fp_neg.append(c.text)
    rows = {}
    for t in (*TECHNIQUES, MIXED):
        row = _prf(tp[t], len(fp_neg), fn[t])
        row["n_pos"] = tp[t] + fn[t]
        row["label_ok"] = round(label_ok[t] / tp[t], 3) if tp[t] else 0.0
        rows[t] = row
    overall = _prf(sum(tp.values()), len(fp_neg), sum(fn.values()))
    overall["n_pos"] = sum(tp.values()) + sum(fn.values())
    overall["n_neg"] = n_neg
    overall["tn"] = n_neg - len(fp_neg)
    overall["label_ok"] = round(sum(label_ok.values()) / sum(tp.values()), 3) if tp else 0.0
    return {"brand": brand, "techniques": rows, "overall": overall, "false_positives": fp_neg, "negative_groups": dict(neg_groups), "misses": {k: v[:5] for k, v in misses.items()}}


def confusion(results: list[dict]) -> dict:
    """Binary confusion matrix (flagged vs not flagged) over every synthetic case, FPR and per-negative-group rates."""
    tp = sum(r["overall"]["tp"] for r in results)
    fn = sum(r["overall"]["fn"] for r in results)
    fp = sum(r["overall"]["fp"] for r in results)
    tn = sum(r["overall"]["tn"] for r in results)
    groups: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        for g, (n, f) in r["negative_groups"].items():
            groups[g][0] += n
            groups[g][1] += f
    return {
        "matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0,
        "false_negative_rate": round(fn / (fn + tp), 4) if fn + tp else 0.0,
        "accuracy": round((tp + tn) / (tp + tn + fp + fn), 4) if tp + tn + fp + fn else 0.0,
        "negative_groups": [{"group": g, "n": n, "flagged": f, "false_positive_rate": round(f / n, 4) if n else 0.0} for g, (n, f) in sorted(groups.items())],
        "total_cases": tp + fp + fn + tn,
    }


def merge(results: list[dict]) -> dict:
    agg: dict[str, dict] = {}
    for t in (*TECHNIQUES, MIXED, "overall"):
        tp = fp = fn = lab = 0
        for r in results:
            row = r["overall"] if t == "overall" else r["techniques"][t]
            tp += row["tp"]
            fp += row["fp"]
            fn += row["fn"]
            lab += round(row.get("label_ok", 0) * row["tp"])
        agg[t] = {**_prf(tp, fp, fn), "n_pos": tp + fn, "label_ok": round(lab / tp, 3) if tp else 0.0}
    return agg

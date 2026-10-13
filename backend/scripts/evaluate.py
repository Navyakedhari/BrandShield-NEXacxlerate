"""Run the name matcher over synthetic cases and print precision / recall / F1 per technique.

Usage (from backend/):  .venv/Scripts/python -m scripts.evaluate [--json] [--seed 7] [--per-technique 25]

Definitions (flagged == predicted positive; review/safe/brand_name == predicted negative):
* Per technique: positives = generated cases using that technique; negatives = ALL hard negatives and
  unrelated names. recall = TP / positives; precision = TP / (TP + flagged negatives).
* label_ok = of the true positives, the share whose reported techniques include the generated technique
  (for `mixed`: the matcher reported two or more techniques).
* Overall: every positive and every negative.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.engine.evaluation import BRANDS, _prf, evaluate_brand, merge  # noqa: E402,F401


def format_table(title: str, rows: dict) -> str:
    lines = [title, f"{'technique':<26}{'n_pos':>6}{'prec':>8}{'recall':>8}{'F1':>8}{'label_ok':>10}"]
    for name, r in rows.items():
        lines.append(f"{name:<26}{r['n_pos']:>6}{r['precision']:>8.3f}{r['recall']:>8.3f}{r['f1']:>8.3f}{r.get('label_ok', 0):>10.3f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print JSON instead of tables")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--per-technique", type=int, default=25)
    ap.add_argument("--brand", action="append", help="brand name (repeatable); default is the built-in set")
    args = ap.parse_args(argv)
    brands = args.brand or BRANDS
    results = [evaluate_brand(b, args.seed, args.per_technique) for b in brands]
    combined = merge(results)
    if args.json:
        print(json.dumps({"seed": args.seed, "brands": results, "combined": combined}, indent=2, ensure_ascii=False))
        return 0
    for r in results:
        print(format_table(f"Brand: {r['brand']}  (negatives: {r['overall']['n_neg']}, false positives: {len(r['false_positives'])})", {**r["techniques"], "OVERALL": r["overall"]}))
        for t, ex in r["misses"].items():
            print(f"  misses [{t}]: " + "; ".join(ex))
        if r["false_positives"]:
            print("  false positives: " + "; ".join(map(repr, r["false_positives"][:8])))
        print()
    print(format_table(f"COMBINED over {len(brands)} brands (seed {args.seed})", {**{k: v for k, v in combined.items() if k != 'overall'}, "OVERALL": combined["overall"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

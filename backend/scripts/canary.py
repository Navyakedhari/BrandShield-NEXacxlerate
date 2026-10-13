"""Canary self-check:  python -m scripts.canary   (run from backend/)

Runs the detection pipeline in memory against a planted fake brand. No database, no network, no keys.
Exit code 0 = pass, 1 = fail. Proves the pipeline logic only, not that live sources work.
"""

from __future__ import annotations

import json
import sys

from app.services.canary import run_canary


def main() -> int:
    result = run_canary()
    for c in result["checks"]:
        print(("PASS " if c["passed"] else "FAIL ") + c["name"] + " - " + c["detail"])
    print(f"\ncanary {'PASSED' if result['passed'] else 'FAILED'} in {result['duration_ms']} ms ({result['scope']})")
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

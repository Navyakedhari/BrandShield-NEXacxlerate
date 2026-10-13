"""MANUAL first live check for Tavily. Makes exactly ONE minimal search (1 credit) and prints the outcome.

Run it yourself after adding TAVILY_API_KEY to backend/.env (the build never ran it):

    cd backend
    .\\.venv\\Scripts\\python -m scripts.live_check                  # prints status only
    .\\.venv\\Scripts\\python -m scripts.live_check --save tavily_real.json   # also saves the raw response

The key is read from the environment / backend/.env and is never printed. The saved raw response contains
only search results (no key); use it to replace the hand-written fixtures in tests/fixtures/tavily/.
If the database is migrated, the call is also counted in the monthly credit counter (api_usage).
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from app.config.config import get_settings
from app.core.collectors.credits import CreditLedger, credit_cost
from app.core.collectors.tavily import ENDPOINT


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", default='"Instagram" official', help="one search query (default is generic)")
    ap.add_argument("--save", metavar="FILE", help="write the raw JSON response to FILE")
    args = ap.parse_args(argv)

    settings = get_settings()
    if settings.tavily_api_key is None:
        print("TAVILY_API_KEY is not set (add it to backend/.env). Nothing was sent.")
        return 2
    key = settings.tavily_api_key.get_secret_value()

    ledger = None
    try:
        from app.db.session import make_engine, make_session_factory

        ledger = CreditLedger(make_session_factory(make_engine(settings.database_url)), settings.tavily_monthly_budget)
        if not ledger.reserve(credit_cost("basic")):
            print("Monthly Tavily budget is used up. Nothing was sent.")
            return 3
    except Exception as exc:  # noqa: BLE001 - the check must still work before migrations are applied
        ledger = None
        print(f"(credit counter not updated: {type(exc).__name__}; run `alembic upgrade head`)")

    body = {
        "query": args.query,
        "search_depth": "basic",
        "max_results": 3,
        "include_domains": ["instagram.com"],
        "exact_match": True,
        "include_usage": True,
    }
    try:
        resp = httpx.post(ENDPOINT, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=20.0)
    except httpx.HTTPError as exc:
        if ledger:
            ledger.refund(1)
        print(f"Request failed before an answer: {type(exc).__name__}. Check your network or proxy.")
        return 1

    print(f"HTTP status: {resp.status_code}")
    meaning = {
        200: "OK", 401: "invalid key (check TAVILY_API_KEY)", 429: "rate limited (see Retry-After header)",
        432: "plan usage limit reached", 433: "pay-as-you-go limit reached",
    }
    print("Meaning:", meaning.get(resp.status_code, "unexpected status"))
    if resp.status_code != 200:
        if ledger and resp.status_code < 500:
            ledger.refund(1)
        return 1
    try:
        data = resp.json()
    except ValueError:
        print("Response was not valid JSON.")
        return 1
    results = data.get("results") if isinstance(data, dict) else None
    print("Results returned:", len(results) if isinstance(results, list) else "unknown (unexpected shape)")
    usage = data.get("usage") if isinstance(data, dict) else None
    print("Credits reported by Tavily:", usage.get("credits") if isinstance(usage, dict) else "not reported")
    if ledger and isinstance(usage, dict) and isinstance(usage.get("credits"), int):
        ledger.record_reported(usage["credits"])
    if isinstance(data, dict):
        print("Top-level fields seen:", ", ".join(sorted(data)))
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Raw response saved to {args.save}")
    if ledger:
        snap = ledger.snapshot()
        print(f"Credit counter this month: {snap['used']} used of {snap['budget']} ({snap['remaining']} remaining)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

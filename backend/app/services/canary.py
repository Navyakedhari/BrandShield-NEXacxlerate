"""Canary: a planted fake that every scan must find.

What it proves: the PIPELINE LOGIC (name matching, allowlist exclusion, scoring, banding, persistence) still works.
What it does not prove: that any live source (Tavily, iTunes, ...) is reachable or returns real data. Use /health and the
Source Health page for that.

Isolation (all asserted by tests):
* runs against a private in-memory SQLite database and a temporary data directory; the real database is never read or written
* the brand is fictional ("Zephyrine Vault Bank") and its rows never exist in the real database, so canary detections can
  never appear in real dashboards or reports
* only fixed in-code candidates plus the sample collector are used; no network call is made (no key, no live collector, no logo fetch)
"""

from __future__ import annotations

import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config.config import Settings
from app.core.collectors.base import BrandQuery, Candidate, CollectContext, Collector
from app.core.collectors.sample import SampleCollector
from app.core.collectors.selection import CollectorRuntime
from app.core.pipeline.scan_pipeline import request_scan
from app.db.base import Base
from app.db.session import make_engine, make_session_factory
from app.models import Brand, Detection, OfficialAccount, OfficialApp, OfficialMatch, Scan, Setting
from app.services.context import build_context

BRAND_NAME = "Zephyrine Vault Bank"
OFFICIAL_HANDLE = "zephyrinevault"
OFFICIAL_PACKAGE = "com.zephyrinevault.mobile"
FAKE_HANDLE = "zephyrinevaultbank_support"  # planted look-alike (social)
FAKE_PACKAGE = "com.zephyrinevaultbank.rewards"  # planted look-alike (app)
CLEAN_HANDLE = "zephyrine_gardens"  # a different business that shares one word: must NOT be flagged
FLAG_MIN_SCORE = 70  # medium band and above


class _Fixed(CollectorRuntime):
    def __init__(self, settings: Settings, session_factory, collectors: list[Collector]) -> None:
        super().__init__(settings, session_factory, now=lambda: datetime.now(UTC))
        self._fixed = collectors

    def build(self, selection, **_kw):  # noqa: ANN001
        return list(self._fixed)


class CanaryCollector(Collector):
    """Returns the planted candidates. Labelled source='sample' so every row it creates is marked as sample data."""

    name = "canary"
    kind = "mixed"
    platforms = ("instagram", "google_play")

    def collect(self, brand: BrandQuery, ctx: CollectContext) -> list[Candidate]:
        now = ctx.now()
        return [
            Candidate(
                platform="instagram", kind="social", handle=FAKE_HANDLE, display_name="Zephyrine Vault Bank Support",
                profile_url=f"https://www.instagram.com/{FAKE_HANDLE}/", source="sample", fetched_at=now,
                bio="Official support desk. DM us to verify your KYC and claim your cashback before your account is blocked.",
                followers=140, following=2900, posts=2, verified=False, created_at=(now - _days(9)).date(),
            ),
            Candidate(
                platform="google_play", kind="app", handle=FAKE_PACKAGE, display_name="Zephyrine Vault Bank Rewards",
                profile_url=f"https://play.google.com/store/apps/details?id={FAKE_PACKAGE}", source="sample", fetched_at=now,
                bio="Claim your reward. Login with your bank password to unlock cashback.", store="google_play",
                publisher="Quick Rewards Studio", rating=2.4, rating_count=31,
            ),
            # official echoes: exact official handle / package id => must be excluded before scoring
            Candidate(
                platform="instagram", kind="social", handle=OFFICIAL_HANDLE, display_name=BRAND_NAME,
                profile_url=f"https://www.instagram.com/{OFFICIAL_HANDLE}/", source="sample", fetched_at=now,
                bio="The official Zephyrine Vault Bank page.", followers=300_000, verified=True,
            ),
            Candidate(
                platform="google_play", kind="app", handle=OFFICIAL_PACKAGE, display_name="Zephyrine Vault Bank Mobile",
                profile_url=f"https://play.google.com/store/apps/details?id={OFFICIAL_PACKAGE}", source="sample", fetched_at=now,
                bio="Official app.", store="google_play", publisher="Zephyrine Vault Bank Ltd", rating=4.5, rating_count=90_000,
            ),
            # hard negative: shares one word with the brand, unrelated business
            Candidate(
                platform="instagram", kind="social", handle=CLEAN_HANDLE, display_name="Zephyrine Gardens",
                profile_url=f"https://www.instagram.com/{CLEAN_HANDLE}/", source="sample", fetched_at=now,
                bio="Family run garden centre. Open 9 to 6, closed Sundays.", followers=5200, following=180, posts=400,
                created_at=(now - _days(2200)).date(),
            ),
        ]


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _settings(data_dir: Path) -> Settings:
    # Explicit values beat the environment: a developer's real keys can never reach the canary.
    return Settings(  # type: ignore[call-arg]
        _env_file=None, jwt_secret="canary-not-a-secret-000000", database_url="sqlite://", data_dir=data_dir, app_env="canary",
        tavily_api_key=None, searxng_base_url=None, redis_url=None, scam_llm_enabled=False,
        scan_executor="inline", scheduler_enabled=False, scan_logo_fetch=False, screenshots_enabled=False,
        reachability_checks_per_scan=0, scan_interval_minutes=0,
    )


def run_canary() -> dict[str, Any]:
    """Run the pipeline in memory against the planted fake. Never raises: an exception becomes a failed check."""
    t0 = time.perf_counter()
    checks: list[dict] = []
    try:
        with tempfile.TemporaryDirectory(prefix="scammind-canary-") as tmp:
            settings = _settings(Path(tmp))
            engine = make_engine("sqlite://")
            try:
                Base.metadata.create_all(engine)
                sf = make_session_factory(engine)
                with sf() as db:
                    brand = Brand(name=BRAND_NAME, aliases=["Zephyrine Vault"], keywords=["zephyrinevault"], domains=["zephyrinevault.example"],
                                  source="sample")
                    db.add(brand)
                    db.flush()
                    db.add(OfficialAccount(brand_id=brand.id, platform="instagram", handle=OFFICIAL_HANDLE, handle_key=OFFICIAL_HANDLE,
                                           url=f"instagram.com/{OFFICIAL_HANDLE}", status="verified", source="sample"))
                    db.add(OfficialApp(brand_id=brand.id, name="Zephyrine Vault Bank Mobile", package_id=OFFICIAL_PACKAGE,
                                       package_key=OFFICIAL_PACKAGE, publisher="Zephyrine Vault Bank Ltd", stores=["google_play"],
                                       status="verified", source="sample"))
                    db.add(Setting(key="data_mode", value="sample"))
                    db.commit()
                    brand_id = brand.id
                runtime = _Fixed(settings, sf, [CanaryCollector(), SampleCollector()])
                ctx = build_context(settings, sf, runtime)
                with sf() as db:
                    scan = request_scan(ctx, db, brand_id=brand_id, scope="all", user=None)  # inline executor: finished on return
                    scan_id = scan.id
                with sf() as db:
                    checks = _evaluate(db, scan_id, brand_id)
            finally:
                engine.dispose()
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("canary_ran", False, f"the canary itself crashed: {type(exc).__name__}"))
    failures = [f"{c['name']}: {c['detail']}" for c in checks if not c["passed"]]
    return {"passed": not failures, "checks": checks, "failures": failures, "duration_ms": int((time.perf_counter() - t0) * 1000),
            "scope": "pipeline logic only (in-memory, no network); does not prove live sources work"}


def _evaluate(db, scan_id: int, brand_id: int) -> list[dict]:
    scan = db.get(Scan, scan_id)
    dets = {d.canonical_key: d for d in db.scalars(select(Detection).where(Detection.brand_id == brand_id))}
    key = lambda p, h: f"{p}:{h}"  # noqa: E731
    fake, fake_app = dets.get(key("instagram", FAKE_HANDLE)), dets.get(key("google_play", FAKE_PACKAGE))
    clean = dets.get(key("instagram", CLEAN_HANDLE))
    matches = {m.match_key for m in db.scalars(select(OfficialMatch).where(OfficialMatch.brand_id == brand_id))}
    out = [_check("scan_completed", scan is not None and scan.state == "completed", f"scan state: {scan.state if scan else 'missing'}")]
    out.append(_check("lookalike_profile_detected", fake is not None and fake.score >= FLAG_MIN_SCORE and fake.status == "new",
                      f"score {fake.score if fake else None}, status {fake.status if fake else None} (need >= {FLAG_MIN_SCORE}, status new)"))
    # Apps are scored without a logo comparison here (no network), so the name + publisher signals alone land in the low band by design;
    # the canary requires the app to be stored for review with exactly those two reasons.
    signals = {r["signal"] for r in fake_app.reasons} if fake_app is not None else set()
    out.append(_check("lookalike_app_detected", fake_app is not None and fake_app.status == "new" and {"name_similarity", "publisher_differs"} <= signals,
                      f"score {fake_app.score if fake_app else None}, status {fake_app.status if fake_app else None}, signals {sorted(signals)}"))
    official_in_list = [d.canonical_key for d in dets.values()
                        if d.handle and d.handle.lstrip("@").lower() in (OFFICIAL_HANDLE, OFFICIAL_PACKAGE)
                        or d.package_id and d.package_id.lower() == OFFICIAL_PACKAGE]
    out.append(_check("official_assets_excluded", not official_in_list and len(matches) >= 2,
                      f"official in detections: {official_in_list or 'none'}; allowlist matches recorded: {len(matches)} (need >= 2)"))
    out.append(_check("unrelated_business_not_flagged", clean is None or clean.score < FLAG_MIN_SCORE,
                      f"score {clean.score if clean else 'not stored'}"))
    explainable = all(sum(r["points"] for r in d.reasons) == d.details.get("raw_total", sum(r["points"] for r in d.reasons))
                      and d.reasons for d in (fake, fake_app) if d is not None)
    out.append(_check("score_is_explained", fake is not None and explainable, "every point of the score has a reason"))
    out.append(_check("results_labelled_sample", all(d.source == "sample" for d in dets.values()),
                      "every canary row is marked source=sample"))
    return out

"""Risk engine: weights_v1 frozen, severity beside band, algorithm version, signals with provenance, unavailable != clean."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.core.collectors.sample import SampleCollector
from app.core.engine import scoring
from app.core.engine.domain_match import analyze_domain
from app.core.engine.llm import LLMIntentResult
from app.core.engine.models import BrandProfileInput, CandidateApp, CandidateSocial, OfficialAssets
from app.core.engine.pipeline import make_matcher
from app.core.engine.risk_engine import (
    ALGORITHM_V1,
    ALGORITHM_V2,
    WEIGHTS_V1,
    WEIGHTS_V2_WEB,
    RiskConfig,
    RiskEngine,
    severity_for,
)
from app.core.engine.scoring import band_for
from app.core.providers.base import CLEAN, FAILED, MATCH, UNKNOWN, ReputationResult
from scripts.seed_test_brand import make_logo
from tests.conftest import ADMIN, headers_for
from tests.scan_helpers import all_detections, make_client, run_scan, set_mode, setup_brand

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 14)
BRAND = BrandProfileInput(name="Acme Bank", aliases=["Acme"])
OFFICIAL = OfficialAssets(domains=["acmebank.in"], publisher_names=["Acme Bank Ltd"])
GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "golden_scores_v1.json"


def engine() -> RiskEngine:
    return RiskEngine(now=lambda: NOW)


def rep(verdict: str, *, checked: bool = True) -> ReputationResult:
    return ReputationResult("https://acmebank.com.evil.test/", verdict, "google_safe_browsing", "v4", NOW if checked else None,
                            ("SOCIAL_ENGINEERING",) if verdict == MATCH else (), None, f"verdict {verdict}")


# ---- weights_v1 is frozen ------------------------------------------------------------------------


def test_weights_v1_are_the_original_scorer_numbers():
    assert WEIGHTS_V1["social"] == {"name": 34, "logo": 28, "added_words": 12, "bio": 28, "account": 18}
    assert WEIGHTS_V1["app"] == {"name": 24, "icon": 30, "publisher": 22, "description": 17, "store": 10}
    assert (WEIGHTS_V1["band_high_min"], WEIGHTS_V1["band_medium_min"]) == (85, 70)
    assert WEIGHTS_V1["social"] == scoring.SOCIAL_MAX and WEIGHTS_V1["app"] == scoring.APP_MAX
    assert (scoring.HIGH_MIN, scoring.MEDIUM_MIN) == (85, 70)


def test_v1_assessment_equals_the_scorer_exactly():
    matcher = make_matcher(BRAND)
    cases = [
        CandidateSocial(platform="instagram", handle="acmebnk_support", display_name="Acme Bnk Support", bio="Official helpline. DM us to verify your KYC and claim a refund.",
                        created_at=date(2026, 10, 1), followers=20, following=3000, posts=1, verified=False, logo_similarity=93),
        CandidateSocial(platform="x", handle="acme_fans", display_name="Acme fans", bio=None),
    ]
    for c in cases:
        name = matcher.match(c.handle or "")
        direct = scoring.score_social(c, OFFICIAL, name, today=TODAY)
        a = engine().assess_social(c, OFFICIAL, name, today=TODAY)
        assert (a.score, a.raw_total, a.band) == (direct.score, direct.raw_total, direct.band)
        assert [r.to_dict() for r in a.reasons] == [r.to_dict() for r in direct.reasons]
        assert a.algorithm_version == ALGORITHM_V1 == "1.0" and a.weights == "weights_v1" and a.scored_at == NOW
    app = CandidateApp(store="third_party_apk", app_id="com.acme.bank.app", title="Acme Bank", publisher="Quick Tools", icon_similarity=95, installs=300)
    name = matcher.match("Acme Bank")
    direct = scoring.score_app(app, BRAND, OFFICIAL, name, today=TODAY, publisher_matcher=matcher)
    a = engine().assess_app(app, BRAND, OFFICIAL, name, today=TODAY, publisher_matcher=matcher)
    assert (a.score, a.raw_total, a.band) == (direct.score, direct.raw_total, direct.band) and a.algorithm_version == "1.0"


def _golden_rows(settings, checker, with_logo: bool) -> list[dict]:
    with make_client(settings, checker, [SampleCollector()]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "sample")
        b = setup_brand(c, h)
        if with_logo:
            assert c.post(f"/api/brands/{b['id']}/logo", files={"file": ("logo.png", make_logo(), "image/png")}, headers=h).status_code == 200
        run_scan(c, h)
        rows = []
        for i in all_detections(c, h):
            if i["category"] == "web":  # web is new in algorithm 2.0; everything else must be byte-for-byte what it was
                continue
            d = c.get(f"/api/detections/{i['id']}", headers=h).json()
            rows.append({"key": [i["platform"], i["handle"] or i["package_id"]], "score": i["score"], "band": i["risk_band"], "raw_total": d["raw_total"],
                         "reasons": [[r["signal"], r["points"]] for r in d["reasons"]]})
            assert d["algorithm_version"] == "1.0" and d["weights"] == "weights_v1" and d["scored_at"]
        return sorted(rows, key=lambda r: (r["key"][0], str(r["key"][1])))


@pytest.mark.parametrize("with_logo", [True, False])
def test_weights_v1_reproduce_the_stored_sample_scores_exactly(settings, checker, with_logo):
    """The golden file was produced by the code BEFORE the risk engine existed (commit a8c7afb + plan). Same scores, bands, reasons."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["logo" if with_logo else "nologo"]
    assert _golden_rows(settings, checker, with_logo) == golden


# ---- severity ------------------------------------------------------------------------------------


@pytest.mark.parametrize("score, sev", [(0, "LOW"), (39, "LOW"), (40, "MEDIUM"), (69, "MEDIUM"), (70, "HIGH"), (84, "HIGH"), (85, "CRITICAL"), (100, "CRITICAL")])
def test_severity_boundaries(score, sev):
    assert severity_for(score) == sev


def test_severity_never_moves_the_old_band_cutoffs():
    for s in range(0, 101):
        band, sev = band_for(s), severity_for(s)
        assert (band == "high") == (sev == "CRITICAL")
        assert (band == "medium") == (sev == "HIGH")
        assert (band == "low") == (sev in ("LOW", "MEDIUM"))


def test_severity_cutoffs_are_configurable_and_validated():
    cfg = RiskConfig(medium_min=30, high_min=60, critical_min=90)
    assert [severity_for(s, cfg) for s in (29, 30, 59, 60, 89, 90)] == ["LOW", "MEDIUM", "MEDIUM", "HIGH", "HIGH", "CRITICAL"]
    with pytest.raises(ValueError):
        RiskConfig(medium_min=70, high_min=60, critical_min=85)
    with pytest.raises(ValueError):
        RiskConfig(medium_min=0, high_min=60, critical_min=85)
    with pytest.raises(ValueError):
        RiskConfig(medium_min=40, high_min=70, critical_min=101)


def test_assessment_carries_severity_beside_band():
    c = CandidateSocial(platform="instagram", handle="acmebnk", display_name="Acme Bnk", logo_similarity=95, bio="DM to claim your reward and verify KYC refund now",
                        created_at=date(2026, 10, 10), followers=10, following=2000, posts=0, verified=False)
    a = engine().assess_social(c, OFFICIAL, make_matcher(BRAND).match("acmebnk"), today=TODAY)
    assert a.band == band_for(a.score) and a.severity == severity_for(a.score)
    assert RiskEngine(RiskConfig(10, 20, 30), now=lambda: NOW).assess_social(c, OFFICIAL, make_matcher(BRAND).match("acmebnk"), today=TODAY).severity == "CRITICAL"


# ---- unavailable vs clean ------------------------------------------------------------------------


def _sig(a, sid):
    return next(s for s in a.signals if s.id == sid)


def test_missing_evidence_is_unavailable_not_clean():
    name = make_matcher(BRAND).match("acmebnk")
    missing = CandidateSocial(platform="instagram", handle="acmebnk", display_name="Acme Bnk")  # no logo, bio, date, counts
    a = engine().assess_social(missing, OFFICIAL, name, today=TODAY)
    for sid in ("logo_similarity", "bio_signals", "account_age", "follower_oddity"):
        s = _sig(a, sid)
        assert s.status == "unavailable" and s.value is None and s.points == 0, sid
    full = CandidateSocial(platform="instagram", handle="acmebnk", display_name="Acme Bnk", logo_similarity=5.0, bio="We sell shoes.", created_at=date(2015, 1, 1),
                           followers=5000, following=100, posts=300, verified=True)
    b = engine().assess_social(full, OFFICIAL, name, today=TODAY)
    for sid in ("logo_similarity", "bio_signals", "account_age", "follower_oddity"):
        s = _sig(b, sid)
        assert s.status == "clean" and s.value == 0.0 and s.points == 0, sid
    assert a.score == b.score  # a missing check adds nothing; it is just not reported as a pass


def test_app_missing_icon_publisher_and_dates_are_unavailable():
    name = make_matcher(BRAND).match("Acme Bank")
    a = engine().assess_app(CandidateApp(store="google_play", app_id="com.x.y", title="Acme Bank"), BRAND, OFFICIAL, name, today=TODAY)
    for sid in ("icon_similarity", "description_overlap", "publisher_differs", "few_installs", "new_listing"):
        assert _sig(a, sid).status == "unavailable", sid


def test_web_reputation_states():
    analysis = analyze_domain("https://acmebank.com.evil.test/", "Acme Bank", ["acmebank.in"])
    base = engine().assess_web(analysis, None)
    assert _sig(base, "url_reputation").status == "unavailable" and _sig(base, "url_reputation").value is None
    clean = engine().assess_web(analysis, rep(CLEAN))
    assert _sig(clean, "url_reputation").status == "clean" and _sig(clean, "url_reputation").value == 0.0
    for verdict in (UNKNOWN, FAILED):
        r = engine().assess_web(analysis, rep(verdict, checked=verdict == FAILED))
        assert _sig(r, "url_reputation").status == "unavailable" and r.score == base.score
    assert clean.score == base.score  # clean and unavailable score the same; they are reported differently
    hit = engine().assess_web(analysis, rep(MATCH))
    assert _sig(hit, "url_reputation").status == "present" and _sig(hit, "url_reputation").points == 45 and _sig(hit, "url_reputation").provenance == "real_api"
    assert hit.raw_total == base.raw_total + 45 and hit.score == min(100, hit.raw_total)


def test_a_reputation_match_is_its_own_signal_not_folded_into_name_points():
    analysis = analyze_domain("https://acmebank.com/", "Acme Bank", ["acmebank.in"])
    hit = engine().assess_web(analysis, rep(MATCH))
    by = {r.signal: r.points for r in hit.reasons}
    assert by["url_reputation"] == WEB_W("url_reputation") and by["domain_similarity"] == analysis.reasons[0].points


def WEB_W(key):  # noqa: N802
    return WEIGHTS_V2_WEB[key]


# ---- version, provenance ------------------------------------------------------------------------


def test_web_assessments_use_algorithm_2_with_their_own_weights():
    a = engine().assess_web(analyze_domain("https://acmebank.com/", "Acme Bank", ["acmebank.in"]), None)
    assert a.algorithm_version == ALGORITHM_V2 == "2.0" and a.weights == "weights_v2_web" and a.scored_at == NOW
    assert a.band == band_for(a.score) and a.severity == severity_for(a.score)


def test_provenance_labels():
    name = make_matcher(BRAND).match("acmebnk")
    c = CandidateSocial(platform="instagram", handle="acmebnk", display_name="Acme Bnk", bio="Contact us on WhatsApp for a refund of your reward", logo_similarity=80)
    real = engine().assess_social(c, OFFICIAL, name, today=TODAY)
    assert {s.provenance for s in real.signals} == {"local_algorithm"}
    synth = engine().assess_social(c, OFFICIAL, name, today=TODAY, synthetic=True)
    assert {s.provenance for s in synth.signals} == {"synthetic"}
    llm = engine().assess_social(c, OFFICIAL, name, today=TODAY, llm=LLMIntentResult(scam_likelihood=0.9, categories=[], rationale="x"))
    assert _sig(llm, "bio_signals").provenance == "real_api"
    web = analyze_domain("https://acmebank.com/", "Acme Bank", ["acmebank.in"])
    w = engine().assess_web(web, rep(CLEAN))
    assert _sig(w, "url_reputation").provenance == "real_api" and _sig(w, "domain_similarity").provenance == "local_algorithm"
    ws = engine().assess_web(web, None, synthetic=True)
    assert {s.provenance for s in ws.signals} == {"synthetic"}


def test_signal_dict_shape_is_json_serialisable():
    a = engine().assess_web(analyze_domain("https://acmebank.com/", "Acme Bank", ["acmebank.in"]), rep(CLEAN))
    d = a.to_dict()
    json.dumps(d)
    assert set(d) >= {"score", "band", "severity", "algorithm_version", "weights", "scored_at", "signals"}
    assert set(d["signals"][0]) == {"id", "status", "value", "points", "max_points", "provenance", "note", "checked_at"}

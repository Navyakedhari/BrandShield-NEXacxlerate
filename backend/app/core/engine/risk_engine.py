"""Risk engine: one explainable assessment per candidate.

What this module adds on top of the unchanged scorer (`scoring.py`):

* `severity` LOW / MEDIUM / HIGH / CRITICAL BESIDE the existing `band` (high / medium / low at 85 / 70). Cutoffs are
  configurable (`RiskConfig`); defaults LOW < 40, MEDIUM 40-69, HIGH 70-84, CRITICAL 85+. HIGH and CRITICAL start at the
  same scores as the old medium and high bands, so nothing about `band` moves.
* `algorithm_version` and `scored_at` on every assessment.
  - "1.0": social and app candidates. Their points come from `score_social` / `score_app` UNCHANGED. The original weights
    are frozen as `WEIGHTS_V1` (a test pins them to the numbers in scoring.py), so every existing score is reproduced exactly.
  - "2.0": web candidates (look-alike domains / URLs): domain signals + URL reputation, with `WEIGHTS_V2_WEB`.
* A list of normalized `Signal`s: `{id, status, value, points, max_points, provenance, note}`.
  - status `present`: the evidence was found and contributed points.
  - status `clean`: it was checked and nothing was found (zero points).
  - status `unavailable`: it could not be checked (no logo, no key, quota, timeout ...). Zero points, and NEVER the same
    as clean: `value` is null so nobody averages a missing answer into a low risk.
* Provenance per signal: `real_api` (a live provider answered), `local_algorithm`, `pretrained_model`, `synthetic`
  (sample data). Sample candidates are labelled `synthetic` on every signal.
The engine only scores. It never confirms anything: a person reviews every detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.config.time import iso_z, utcnow
from app.core.providers.base import LOCAL_ALGORITHM, REAL_API, SYNTHETIC, ReputationResult

from .domain_match import WEB_MAX, DomainAnalysis
from .llm import LLMIntentResult
from .models import BrandProfileInput, CandidateApp, CandidateSocial, OfficialAssets
from .name_match import NameMatcher, NameMatchResult
from .scoring import APP_MAX, HIGH_MIN, MEDIUM_MIN, SOCIAL_MAX, Reason, ScoreResult, band_for, score_app, score_social

ALGORITHM_V1 = "1.0"
ALGORITHM_V2 = "2.0"
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# The original ScamMind weights, frozen. tests/engine/test_risk_engine.py pins them to scoring.py.
WEIGHTS_V1 = {"social": dict(SOCIAL_MAX), "app": dict(APP_MAX), "band_high_min": HIGH_MIN, "band_medium_min": MEDIUM_MIN}
# New weights for web signals (need the owner's approval, see docs/DECISIONS.md). Points add up to more than 100 on purpose.
WEIGHTS_V2_WEB = {**WEB_MAX, "url_reputation": 45}


@dataclass(frozen=True)
class RiskConfig:
    medium_min: int = 40
    high_min: int = 70
    critical_min: int = 85
    web_weights: dict[str, int] = field(default_factory=lambda: dict(WEIGHTS_V2_WEB))

    def __post_init__(self) -> None:
        if not 0 < self.medium_min < self.high_min < self.critical_min <= 100:
            raise ValueError("severity cutoffs must satisfy 0 < medium < high < critical <= 100")


DEFAULT_CONFIG = RiskConfig()


def severity_for(score: int, config: RiskConfig = DEFAULT_CONFIG) -> str:
    if score >= config.critical_min:
        return "CRITICAL"
    if score >= config.high_min:
        return "HIGH"
    if score >= config.medium_min:
        return "MEDIUM"
    return "LOW"


@dataclass(frozen=True)
class Signal:
    id: str
    status: str  # present | clean | unavailable
    points: int = 0
    max_points: int = 0
    provenance: str = LOCAL_ALGORITHM
    note: str = ""
    checked_at: str | None = None

    @property
    def value(self) -> float | None:
        """0-1 share of the maximum; None when the signal could not be evaluated."""
        if self.status == "unavailable":
            return None
        return round(self.points / self.max_points, 3) if self.max_points else 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "value": self.value, "points": self.points, "max_points": self.max_points,
                "provenance": self.provenance, "note": self.note, "checked_at": self.checked_at}


@dataclass
class RiskAssessment:
    score: int
    raw_total: int
    band: str
    severity: str
    algorithm_version: str
    weights: str  # weights_v1 | weights_v2_web
    scored_at: datetime
    reasons: list[Reason]
    signals: list[Signal]

    @property
    def capped(self) -> bool:
        return self.raw_total > self.score

    @property
    def score_result(self) -> ScoreResult:
        return ScoreResult(self.score, self.raw_total, self.band, self.reasons)

    def to_dict(self) -> dict:
        return {
            "score": self.score, "raw_total": self.raw_total, "capped": self.capped, "band": self.band, "severity": self.severity,
            "algorithm_version": self.algorithm_version, "weights": self.weights, "scored_at": iso_z(self.scored_at),
            "signals": [s.to_dict() for s in self.signals],
        }


def _provenance(default: str, synthetic: bool) -> str:
    return SYNTHETIC if synthetic else default


def _signals_from_reasons(reasons: list[Reason], unavailable: dict[str, str], catalog: list[str], *, synthetic: bool,
                          overrides: dict[str, str] | None = None) -> list[Signal]:
    """One signal per reason (present when it scored), plus clean / unavailable entries for the catalogue ids that did not score."""
    overrides = overrides or {}
    out: list[Signal] = []
    seen: set[str] = set()
    for r in reasons:
        seen.add(r.signal)
        if r.points > 0:
            status = "present"
        elif r.signal in unavailable:
            status = "unavailable"
        else:
            status = "clean"
        out.append(Signal(r.signal, status, r.points, r.max_points, _provenance(overrides.get(r.signal, LOCAL_ALGORITHM), synthetic), r.note))
    for sid in catalog:
        if sid in seen:
            continue
        if sid in unavailable:
            out.append(Signal(sid, "unavailable", 0, 0, _provenance(LOCAL_ALGORITHM, synthetic), unavailable[sid]))
        else:
            out.append(Signal(sid, "clean", 0, 0, _provenance(LOCAL_ALGORITHM, synthetic), "Checked: nothing found."))
    return out


class RiskEngine:
    def __init__(self, config: RiskConfig = DEFAULT_CONFIG, *, now=utcnow) -> None:
        self.config = config
        self._now = now

    def _wrap(self, result: ScoreResult, version: str, weights: str, signals: list[Signal]) -> RiskAssessment:
        return RiskAssessment(result.score, result.raw_total, band_for(result.score), severity_for(result.score, self.config), version, weights,
                              self._now(), list(result.reasons), signals)

    # -- social (algorithm 1.0, weights_v1) --------------------------------------------------------
    def assess_social(
        self, candidate: CandidateSocial, official: OfficialAssets, name: NameMatchResult, *, today: date | None = None,
        llm: LLMIntentResult | None = None, official_description: str | None = None, synthetic: bool = False,
    ) -> RiskAssessment:
        result = score_social(candidate, official, name, today=today, llm=llm, official_description=official_description)
        missing: dict[str, str] = {}
        if candidate.logo_similarity is None:
            missing["logo_similarity"] = "No logo comparison was possible (no picture available)."
        if not (candidate.bio or "").strip() and not candidate.bio_links:
            missing["bio_signals"] = "No bio text was available to analyse."
        if candidate.created_at is None:
            missing["account_age"] = "The account creation date is not known."
        if candidate.followers is None or candidate.following is None:
            missing["follower_oddity"] = "Follower and following counts are not known."
        catalog = ["name_similarity", "logo_similarity", "bio_signals", "account_age", "follower_oddity"]
        over = {"bio_signals": REAL_API} if llm is not None and llm.scam_likelihood >= 0.6 else {}
        return self._wrap(result, ALGORITHM_V1, "weights_v1", _signals_from_reasons(result.reasons, missing, catalog, synthetic=synthetic, overrides=over))

    # -- apps (algorithm 1.0, weights_v1) ----------------------------------------------------------
    def assess_app(
        self, candidate: CandidateApp, brand: BrandProfileInput, official: OfficialAssets, name: NameMatchResult, *,
        today: date | None = None, publisher_matcher: NameMatcher | None = None, synthetic: bool = False,
    ) -> RiskAssessment:
        result = score_app(candidate, brand, official, name, today=today, publisher_matcher=publisher_matcher)
        missing: dict[str, str] = {}
        if candidate.icon_similarity is None:
            missing["icon_similarity"] = "No icon comparison was possible (no icon available)."
        if not brand.description or not candidate.description:
            missing["description_overlap"] = "The official or the candidate description is missing."
        if not (candidate.publisher or "").strip() or not official.publisher_names:
            missing["publisher_differs"] = "The publisher of the candidate or of the brand is not known."
        if candidate.installs is None:
            missing["few_installs"] = "The install count is not known."
        if candidate.published_at is None:
            missing["new_listing"] = "The listing date is not known."
        catalog = ["name_similarity", "icon_similarity", "description_overlap", "publisher_differs", "few_installs", "new_listing"]
        return self._wrap(result, ALGORITHM_V1, "weights_v1", _signals_from_reasons(result.reasons, missing, catalog, synthetic=synthetic))

    # -- web (algorithm 2.0, weights_v2_web) -------------------------------------------------------
    def assess_web(self, analysis: DomainAnalysis, reputation: ReputationResult | None = None, *, synthetic: bool = False,
                   reputation_note: str | None = None) -> RiskAssessment:
        """Domain look-alike points + URL reputation. `reputation` None means it was not checked (unavailable, not clean).

        A reputation MATCH is its own signal (45 points); it is not folded into the name points. A clean or unavailable
        reputation adds nothing, but the two are reported differently."""
        w = self.config.web_weights
        reasons = list(analysis.reasons)
        signals_missing: dict[str, str] = {}
        rep_status, rep_note, rep_at, rep_prov = "unavailable", reputation_note or "URL reputation was not checked.", None, REAL_API
        if reputation is not None:
            rep_status = reputation.signal_status
            rep_note = reputation.message
            rep_at = iso_z(reputation.checked_at) if reputation.checked_at else None
            if rep_status == "present":
                reasons.append(Reason("url_reputation", w["url_reputation"], w["url_reputation"], reputation.message))
        raw = sum(r.points for r in reasons)
        score = min(100, raw)
        result = ScoreResult(score, raw, band_for(score), reasons)
        sigs = _signals_from_reasons(reasons, signals_missing, ["domain_similarity", "deceptive_subdomain", "idn_homograph"], synthetic=synthetic)
        sigs = [s for s in sigs if s.id != "url_reputation"]
        sigs.append(Signal("url_reputation", rep_status, w["url_reputation"] if rep_status == "present" else 0, w["url_reputation"],
                           SYNTHETIC if synthetic else rep_prov, rep_note, rep_at))
        return self._wrap(result, ALGORITHM_V2, "weights_v2_web", sigs)

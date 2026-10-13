"""Local baseline: deterministic, offline, always available. Never calls a model.

Output is labelled `deterministic_similarity`. It reuses the existing phrase rules (`engine.text_signals.analyze_bio`) and trigram overlap
and adds RapidFuzz token ratios against the brand's own description. It is informational: the scorer already counts these phrase rules
and the description overlap, so this finding adds NO points (no double counting)."""

from __future__ import annotations

from rapidfuzz import fuzz

from app.config.time import iso_z, utcnow
from app.core.engine.text_signals import analyze_bio, description_overlap, scam_points

from .prompt import sanitize
from .schema import DETERMINISTIC, ProviderStatus, TextFinding, TextRequest


class LocalTextProvider:
    name = "local"
    label = "Local baseline (RapidFuzz and phrase rules)"

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.name, self.label, True, "rapidfuzz+rules", needs_key=False)

    def analyze(self, req: TextRequest) -> TextFinding:
        text = (req.text or "").strip()
        if not text:
            return TextFinding(self.name, DETERMINISTIC, "unavailable", "rapidfuzz+rules", reason="no_text", message="There was no text to analyse.")
        sigs = analyze_bio(sanitize(text, 4000))
        points = scam_points(sigs)
        sim: dict = {"scam_phrase_ids": [s.id for s in sigs], "scam_phrase_points": points}
        if req.brand_description:
            a, b = text.lower(), req.brand_description.lower()
            ov = description_overlap(text, req.brand_description, min_ngrams=2)
            sim.update({
                "token_set_ratio": round(fuzz.token_set_ratio(a, b), 1),
                "partial_ratio": round(fuzz.partial_ratio(a, b), 1) if len(a) <= 2000 and len(b) <= 2000 else None,
                "trigram_overlap": ov.score, "shared_phrases": ov.shared_phrases[:3],
            })
        else:
            sim["note"] = "No official brand description is stored, so no text comparison was made."
        # a rough 0-1 reading of the phrase rules only (30 points of rules ~ certain)
        return TextFinding(self.name, DETERMINISTIC, "ok", "rapidfuzz+rules", scam_likelihood=round(min(1.0, points / 30), 2),
                           categories=[], rationale=f"{len(sigs)} scam phrase rule(s) matched.", similarity=sim,
                           checked_at=iso_z(utcnow()))

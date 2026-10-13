"""Look-alike variants of a brand name, used to widen search queries.

Uses the same techniques as the detection engine's synthetic generator (character swap, digit swap,
transposed/missing letters, spacing). Deterministic for a given name and seed. Variants are plain ASCII so
they are usable as search terms.
"""

from __future__ import annotations

from app.core.engine.name_match import (
    CHARACTER_SWAP,
    DIGIT_OR_SYMBOL_SWAP,
    MISSING_OR_EXTRA_LETTER,
    SPACING_OR_SEPARATOR,
    TRANSPOSED,
)
from app.core.engine.synthetic import MIXED, generate_cases

_ORDER = (DIGIT_OR_SYMBOL_SWAP, TRANSPOSED, MISSING_OR_EXTRA_LETTER, CHARACTER_SWAP, SPACING_OR_SEPARATOR, MIXED)


def generate_variants(name: str, limit: int = 6, seed: int = 11) -> list[str]:
    """Up to `limit` distinct look-alike spellings of `name`, one technique at a time (round robin)."""
    name = " ".join((name or "").split())
    if len(name) < 3 or limit <= 0:
        return []
    cases = generate_cases(name, seed=seed, per_technique=6)
    by_tech: dict[str, list[str]] = {t: [] for t in _ORDER}
    for c in cases:
        if not c.expect_flagged or c.technique not in by_tech:
            continue
        text = " ".join(c.text.split())
        # search-friendly: ASCII, no separators-only forms, not just the brand again
        if not text.isascii() or not any(ch.isalnum() for ch in text) or text.lower() == name.lower():
            continue
        by_tech[c.technique].append(text)
    out: list[str] = []
    i = 0
    while len(out) < limit and any(by_tech.values()):
        tech = _ORDER[i % len(_ORDER)]
        i += 1
        if by_tech[tech]:
            v = by_tech[tech].pop(0)
            if v.lower() not in {o.lower() for o in out}:
                out.append(v)
    return out

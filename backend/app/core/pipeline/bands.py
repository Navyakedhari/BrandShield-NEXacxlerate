HIGH_MIN = 85
MEDIUM_MIN = 70


def band_for(score: int) -> str:
    """85+ high, 70-84 medium, below 70 low."""
    if score >= HIGH_MIN:
        return "high"
    if score >= MEDIUM_MIN:
        return "medium"
    return "low"

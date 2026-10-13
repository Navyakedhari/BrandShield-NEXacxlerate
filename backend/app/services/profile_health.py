"""Deterministic brand profile health score (0-100) with a reason for everything missing."""
from app.models import Brand

# (key, label, weight, why-it-matters when missing)
CHECKS = [
    ("name_aliases", "Brand name and aliases", 20, "Add at least one alias so spelling variants of your name can be matched."),
    ("keywords", "Keywords", 10, "Add keywords or hashtags people use for your brand."),
    ("logo", "Official logo meets requirements", 20, "Upload a square logo (PNG, JPEG or WebP, at least 256 px) for logo matching."),
    ("domains", "Official domains added", 15, "Add your official domains so links to other sites are treated as suspicious."),
    ("accounts", "Official social accounts added", 15, "Add your official accounts so they are never flagged and fakes can be compared."),
    ("apps_publisher", "Apps include publisher names", 10, "Add your official apps with the publisher name to catch fake apps."),
    ("urls_reachable", "Official account URLs reachable", 10, "Add accounts first, then make sure each URL can be reached."),
]


def _message(percent: int, missing: int) -> str:
    if percent == 100:
        return "Complete. Every check passes."
    if percent >= 80:
        return f"Good coverage. Fix {missing} item{'s' if missing != 1 else ''} to reach 100%."
    if percent >= 50:
        return f"Partial coverage. {missing} items need attention."
    return "Low coverage. Complete the missing items so scans can compare against what is official."


def compute_profile_health(brand: Brand) -> dict:
    accounts = list(brand.accounts)
    apps = list(brand.apps)
    results = {
        "name_aliases": bool(brand.name.strip()) and len(brand.aliases) > 0,
        "keywords": len(brand.keywords) > 0,
        "logo": brand.logo_path is not None,
        "domains": len(brand.domains) > 0,
        "accounts": len(accounts) > 0,
        "apps_publisher": len(apps) > 0 and all(a.publisher.strip() for a in apps),
        "urls_reachable": len(accounts) > 0 and all(a.status == "verified" for a in accounts),
    }
    checks = []
    missing = []
    total = 0
    for key, label, weight, why in CHECKS:
        ok = results[key]
        points = weight if ok else 0
        total += points
        why_text = None if ok else why
        if key == "urls_reachable" and accounts and not ok:
            bad = ", ".join(display for display in (f"{a.handle} on {a.platform}" for a in accounts if a.status != "verified"))
            label = "Some official account URLs could not be reached"
            why_text = f"Could not reach: {bad}. Check the handle or try again later."
        checks.append({"key": key, "label": label, "ok": ok, "weight": weight, "points": points, "why": why_text})
        if not ok:
            missing.append({"key": key, "label": label, "why": why_text})
    return {"percent": total, "message": _message(total, len(missing)), "checks": checks, "missing": missing}

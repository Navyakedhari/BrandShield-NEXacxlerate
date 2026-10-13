"""Pure validation/normalisation helpers (no I/O)."""
import re

ACCOUNT_PLATFORMS = ("instagram", "x", "facebook", "linkedin", "youtube")
APP_STORES = ("google_play", "app_store")

_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{3,30}$")
_HANDLE_HYPHEN_RE = re.compile(r"^[A-Za-z0-9._-]{3,100}$")  # LinkedIn company slugs and YouTube handles may contain hyphens
_HYPHEN_PLATFORMS = {"linkedin", "youtube"}
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
# Google Play application ID: 2+ dot separated segments, each starting with a letter.
_GOOGLE_PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
# Apple bundle ID: reverse-DNS, letters digits hyphen and period only.
_APPLE_BUNDLE_RE = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")

URL_PREFIX = {
    "instagram": ("instagram.com/", ""),
    "x": ("x.com/", ""),
    "facebook": ("facebook.com/", ""),
    "linkedin": ("linkedin.com/company/", ""),
    "youtube": ("youtube.com/@", ""),
}
_AT_PLATFORMS = {"instagram", "x", "youtube"}  # display handle with a leading @ (API.md 5.3)


def normalize_handle(raw: str, platform: str | None = None) -> str:
    """Strip whitespace and leading @; raise ValueError if not 3-30 of letters/digits/dot/underscore.

    LinkedIn slugs and YouTube handles may also contain hyphens (LinkedIn up to 100 characters)."""
    handle = raw.strip()
    if handle.startswith("@"):
        handle = handle[1:]
    if platform in _HYPHEN_PLATFORMS:
        if not _HANDLE_HYPHEN_RE.fullmatch(handle) or (platform == "youtube" and len(handle) > 30):
            raise ValueError("Handle must be 3 to 30 characters: letters, digits, dot, underscore or hyphen")
        return handle
    if not _HANDLE_RE.fullmatch(handle):
        raise ValueError("Handle must be 3 to 30 characters: letters, digits, dot or underscore")
    return handle


def display_handle(platform: str, handle: str) -> str:
    return f"@{handle}" if platform in _AT_PLATFORMS else handle


def build_account_url(platform: str, handle: str) -> str:
    return f"{URL_PREFIX[platform][0]}{handle}"


def normalize_domain(raw: str) -> str:
    """Return a lower-case hostname or raise ValueError. Rejects schemes, paths, ports, IP literals."""
    d = raw.strip().lower().rstrip(".")
    if not d or len(d) > 253:
        raise ValueError(f"'{raw}' is not a valid domain")
    if any(c in d for c in "/:@? #\\"):
        raise ValueError(f"'{raw}' is not a valid domain: use only the hostname, e.g. example.com")
    labels = d.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.fullmatch(lb) for lb in labels) or not _TLD_RE.fullmatch(labels[-1]):
        raise ValueError(f"'{raw}' is not a valid domain")
    return d


def clean_text_list(items: list[str], *, max_items: int = 50, max_len: int = 80, what: str = "item") -> list[str]:
    """Trim, drop blanks, de-duplicate case-insensitively (first spelling wins)."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        t = " ".join(item.split())
        if not t:
            continue
        if len(t) > max_len:
            raise ValueError(f"Each {what} must be at most {max_len} characters")
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    if len(out) > max_items:
        raise ValueError(f"At most {max_items} {what}s allowed")
    return out


def validate_package_id(package_id: str, stores: list[str]) -> str:
    pkg = package_id.strip()
    if not pkg or len(pkg) > 155:
        raise ValueError("Package or bundle ID is required (max 155 characters)")
    if "google_play" in stores and not _GOOGLE_PKG_RE.fullmatch(pkg):
        raise ValueError(
            "Not a valid Google Play package name (e.g. com.example.app: dot separated, each part starts with a letter)"
        )
    if "app_store" in stores and not _APPLE_BUNDLE_RE.fullmatch(pkg):
        raise ValueError("Not a valid App Store bundle ID (e.g. com.example.app: letters, digits, hyphen, dot)")
    return pkg


_APPLE_ID_RE = re.compile(r"^[0-9]{6,15}$")
_APPLE_URL_ID_RE = re.compile(r"^https?://(?:apps|itunes)\.apple\.com/.*?/?id([0-9]{6,15})(?:[/?#].*)?$", re.I)


def normalize_apple_app_id(raw: str) -> str:
    """The numeric App Store id from `123456789`, `id123456789` or an apps.apple.com/.../id123456789 URL. Raises ValueError otherwise."""
    t = (raw or "").strip()
    m = _APPLE_URL_ID_RE.match(t)
    if m:
        return m.group(1)
    if t.lower().startswith("id"):
        t = t[2:]
    if not _APPLE_ID_RE.fullmatch(t):
        raise ValueError("Not an App Store id: use the number from the listing URL (apps.apple.com/.../id123456789) or paste the URL")
    return t

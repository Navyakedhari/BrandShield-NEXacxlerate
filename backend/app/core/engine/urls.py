"""Parse social profile URLs and app-store listing URLs. No network access."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

PLATFORMS = ("instagram", "facebook", "x", "telegram", "linkedin", "youtube")
MIRROR_STORES = frozenset({"apkpure", "apkmirror", "uptodown", "aptoide", "apkcombo", "apkmonk"})


@dataclass(frozen=True)
class ParsedUrl:
    ok: bool
    kind: str | None = None  # "profile" | "store"
    platform: str | None = None  # profile: instagram|facebook|x|telegram|linkedin|youtube
    store: str | None = None  # store: google_play|apple_app_store|apkpure|apkmirror|uptodown|aptoide|apkcombo|apkmonk
    handle: str | None = None  # profile handle / id, or app/package id for stores
    subkind: str | None = None  # linkedin: company|in|school; youtube: handle|c|user|channel; facebook: name|id
    slug: str | None = None  # apple store slug
    canonical_url: str | None = None
    reason: str | None = None  # why rejected

    @property
    def is_mirror(self) -> bool:
        return self.store in MIRROR_STORES


def _reject(reason: str) -> ParsedUrl:
    return ParsedUrl(ok=False, reason=reason)


def _split(url: str):
    """Return (host, segments, query) or an error string."""
    raw = (url or "").strip()
    if not raw:
        return "Empty URL."
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        scheme = raw.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            return f"Unsupported scheme '{scheme}'."
    else:
        raw = "https://" + raw.lstrip("/")
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower().rstrip(".")
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return "Malformed URL."
    if not host or "." not in host:
        return "No valid host in URL."
    path = unquote(parts.path or "")
    segs = [s for s in path.split("/") if s]
    return host, segs, parse_qs(parts.query)


def _host_is(host: str, *domains: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


_IG_RESERVED = {
    "explore", "p", "reel", "reels", "stories", "story", "accounts", "direct", "tv", "share", "s", "about", "legal", "web",
    "challenge", "developer", "directory", "emailsignup", "login", "tags", "hashtag", "locations", "oauth", "api", "ar", "press", "privacy",
}  # fmt: skip
_FB_RESERVED = {
    "sharer", "sharer.php", "share", "share.php", "groups", "watch", "marketplace", "events", "login", "login.php", "hashtag", "permalink.php",
    "photo", "photo.php", "story.php", "reel", "reels", "stories", "gaming", "help", "policies", "privacy", "dialog", "plugins", "tr", "ads",
    "business", "l.php", "flx", "video", "videos", "posts", "search", "public", "pages", "people", "profile.php", "home.php", "friends", "settings",
    "notifications", "messages", "bookmarks", "recover", "r.php", "about", "careers", "terms",
}  # fmt: skip
_X_RESERVED = {
    "i", "home", "explore", "search", "hashtag", "share", "intent", "login", "logout", "signup", "settings", "messages", "notifications",
    "compose", "tos", "privacy", "about", "download", "jobs", "lists", "bookmarks", "communities", "topics", "moments", "who_to_follow",
    "account", "oauth", "i/flow", "widgets", "tweet", "status", "en", "rules", "help",
}  # fmt: skip
_TG_RESERVED = {
    "joinchat", "addstickers", "addemoji", "share", "proxy", "socks", "setlanguage", "iv", "login", "c", "s", "contact", "boost", "invoice",
    "addlist", "confirmphone", "bg", "addtheme", "msg", "dl", "apps", "blog", "faq", "privacy", "tos", "auth", "web", "k", "a",
}  # fmt: skip
_LI_RESERVED = {"feed", "jobs", "pub", "posts", "pulse", "learning", "groups", "showcase", "events", "search", "sales", "help", "legal", "uas", "login", "signup", "company-beta", "directory"}  # fmt: skip
_YT_RESERVED = {
    "watch", "shorts", "playlist", "results", "feed", "embed", "live", "hashtag", "gaming", "premium", "account", "channel_switcher", "t",
    "redirect", "attribution_link", "about", "kids", "music", "trending", "tv", "oembed", "post", "v", "e",
}  # fmt: skip

_IG_HANDLE = re.compile(r"^[a-z0-9._]{1,30}$")
_FB_HANDLE = re.compile(r"^[a-z0-9.\-]{5,50}$")
_X_HANDLE = re.compile(r"^[a-z0-9_]{1,15}$")
_TG_HANDLE = re.compile(r"^[a-z][a-z0-9_]{3,31}$")
_LI_SLUG = re.compile(r"^[a-z0-9À-￿%._\-]{1,100}$")
_YT_HANDLE = re.compile(r"^[a-z0-9._\-]{3,30}$")
_YT_CHANNEL = re.compile(r"^UC[A-Za-z0-9_\-]{22}$")
_PKG = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")


def _ok_profile(platform: str, handle: str, canonical: str, subkind: str | None = None) -> ParsedUrl:
    return ParsedUrl(ok=True, kind="profile", platform=platform, handle=handle, subkind=subkind, canonical_url=canonical)


def parse_profile_url(url: str) -> ParsedUrl:
    """Profile/page URL -> platform, handle (or id) and canonical URL; otherwise a rejection with reason."""
    got = _split(url)
    if isinstance(got, str):
        return _reject(got)
    host, segs, query = got

    if _host_is(host, "instagram.com", "instagr.am"):
        return _instagram(segs)
    if _host_is(host, "facebook.com", "fb.com", "fb.me"):
        return _facebook(host, segs, query)
    if _host_is(host, "twitter.com", "x.com"):
        return _x(segs)
    if _host_is(host, "t.me", "telegram.me", "telegram.dog"):
        return _telegram(segs)
    if _host_is(host, "linkedin.com"):
        return _linkedin(segs)
    if _host_is(host, "youtube.com"):
        return _youtube(segs)
    if host == "youtu.be":
        return _reject("youtu.be links point to a video, not a channel.")
    return _reject(f"Unsupported host '{host}'.")


def _instagram(segs: list[str]) -> ParsedUrl:
    if not segs:
        return _reject("No profile in path.")
    first = segs[0].lower()
    if first in _IG_RESERVED:
        return _reject(f"'/{first}' is not a profile path.")
    if not _IG_HANDLE.match(first):
        return _reject("Not a valid Instagram handle.")
    return _ok_profile("instagram", first, f"https://www.instagram.com/{first}/")


def _facebook(host: str, segs: list[str], query: dict) -> ParsedUrl:
    if not segs:
        return _reject("No page in path.")
    first = segs[0].lower()
    if first == "profile.php":
        ids = query.get("id", [])
        if ids and ids[0].isdigit():
            return _ok_profile("facebook", ids[0], f"https://www.facebook.com/profile.php?id={ids[0]}", "id")
        return _reject("profile.php without a numeric id.")
    if first in ("pages", "people"):
        ids = [s for s in segs[1:] if s.isdigit() and len(s) >= 5]
        if ids:
            return _ok_profile("facebook", ids[-1], f"https://www.facebook.com/profile.php?id={ids[-1]}", "id")
        return _reject(f"'/{first}/' path has no numeric page id.")
    if first == "groups":
        return _reject("Facebook groups are not pages or profiles.")
    if first in _FB_RESERVED:
        return _reject(f"'/{first}' is not a profile path.")
    if not _FB_HANDLE.match(first):
        return _reject("Not a valid Facebook page name.")
    return _ok_profile("facebook", first, f"https://www.facebook.com/{first}", "name")


def _x(segs: list[str]) -> ParsedUrl:
    if not segs:
        return _reject("No profile in path.")
    first = segs[0].lstrip("@").lower()
    if first in _X_RESERVED or segs[0].lower() in _X_RESERVED:
        return _reject(f"'/{first}' is not a profile path.")
    if not _X_HANDLE.match(first):
        return _reject("Not a valid X/Twitter handle.")
    return _ok_profile("x", first, f"https://x.com/{first}")


def _telegram(segs: list[str]) -> ParsedUrl:
    if not segs:
        return _reject("No channel in path.")
    if segs[0] == "s" and len(segs) > 1:
        segs = segs[1:]  # t.me/s/<channel> is the public preview of a channel
    first = segs[0]
    low = first.lower()
    if first.startswith("+"):
        return _reject("Invite links (+code) have no public handle.")
    if low in _TG_RESERVED:
        return _reject(f"'/{low}' is not a public channel or user path.")
    if not _TG_HANDLE.match(low):
        return _reject("Not a valid Telegram username.")
    return _ok_profile("telegram", low, f"https://t.me/{low}")


def _linkedin(segs: list[str]) -> ParsedUrl:
    if len(segs) < 2:
        return _reject("LinkedIn profile URLs look like /company/<name>, /in/<name> or /school/<name>.")
    kind = segs[0].lower()
    if kind in _LI_RESERVED or kind not in ("company", "in", "school"):
        return _reject(f"'/{kind}' is not a company, person or school page.")
    slug = segs[1].lower()
    if not _LI_SLUG.match(slug):
        return _reject("Not a valid LinkedIn slug.")
    return _ok_profile("linkedin", slug, f"https://www.linkedin.com/{kind}/{slug}", kind)


def _youtube(segs: list[str]) -> ParsedUrl:
    if not segs:
        return _reject("No channel in path.")
    first = segs[0]
    if first.startswith("@"):
        h = first[1:].lower()
        if not _YT_HANDLE.match(h):
            return _reject("Not a valid YouTube handle.")
        return _ok_profile("youtube", h, f"https://www.youtube.com/@{h}", "handle")
    low = first.lower()
    if low in ("c", "user") and len(segs) > 1:
        name = segs[1].lower()
        return _ok_profile("youtube", name, f"https://www.youtube.com/{low}/{name}", low)
    if low == "channel" and len(segs) > 1:
        if not _YT_CHANNEL.match(segs[1]):
            return _reject("Not a valid YouTube channel id (should start with UC).")
        return _ok_profile("youtube", segs[1], f"https://www.youtube.com/channel/{segs[1]}", "channel")
    if low in _YT_RESERVED:
        return _reject(f"'/{low}' is not a channel path.")
    return _reject("Unrecognised YouTube path; expected /@handle, /c/, /user/ or /channel/.")


# ---- stores ----------------------------------------------------------------------------------------


def _ok_store(store: str, app_id: str, canonical: str, slug: str | None = None) -> ParsedUrl:
    return ParsedUrl(ok=True, kind="store", store=store, handle=app_id, slug=slug, canonical_url=canonical)


def parse_store_url(url: str) -> ParsedUrl:
    """App-store listing URL -> (store, package/app id); otherwise a rejection with reason."""
    raw = (url or "").strip()
    m = re.match(r"^market://details\?(.*)$", raw, re.I)
    if m:
        ids = parse_qs(m.group(1)).get("id", [])
        if ids and _PKG.match(ids[0]):
            return _ok_store("google_play", ids[0], f"https://play.google.com/store/apps/details?id={ids[0]}")
        return _reject("market:// link without a valid package id.")
    got = _split(url)
    if isinstance(got, str):
        return _reject(got)
    host, segs, query = got

    if host == "play.google.com":
        if len(segs) >= 3 and segs[0] == "store" and segs[1] == "apps" and segs[2] == "details":
            ids = query.get("id", [])
            if ids and _PKG.match(ids[0]):
                return _ok_store("google_play", ids[0], f"https://play.google.com/store/apps/details?id={ids[0]}")
            return _reject("Google Play URL without a valid id= package parameter.")
        return _reject("Not a Google Play app details URL.")

    if host in ("apps.apple.com", "itunes.apple.com"):
        m = next((re.fullmatch(r"id(\d{5,})", s) for s in segs if re.fullmatch(r"id(\d{5,})", s)), None)
        if "app" not in segs or not m:
            ids = query.get("id", [])
            if host == "itunes.apple.com" and ids and ids[0].isdigit():
                return _ok_store("apple_app_store", ids[0], f"https://apps.apple.com/app/id{ids[0]}")
            return _reject("Not an App Store app URL (expected /app/<slug>/id<digits>).")
        app_id = m.group(1)
        idx = segs.index(m.group(0))
        slug = segs[idx - 1].lower() if idx >= 1 and segs[idx - 1] != "app" else None
        canonical = f"https://apps.apple.com/app/{slug}/id{app_id}" if slug else f"https://apps.apple.com/app/id{app_id}"
        return _ok_store("apple_app_store", app_id, canonical, slug)

    if _host_is(host, "apkpure.com", "apkpure.net", "apkpure.fun"):
        pkg = next((s for s in reversed(segs) if _PKG.match(s)), None)
        if pkg:
            return _ok_store("apkpure", pkg, f"https://apkpure.com/{segs[-2] if len(segs) >= 2 and segs[-1] == pkg else 'app'}/{pkg}")
        return _reject("APKPure URL without a package id.")

    if _host_is(host, "apkcombo.com", "apkcombo.app"):
        pkg = next((s for s in reversed(segs) if _PKG.match(s)), None)
        if pkg:
            return _ok_store("apkcombo", pkg, f"https://apkcombo.com/app/{pkg}")
        return _reject("APKCombo URL without a package id.")

    if _host_is(host, "apkmirror.com"):
        if len(segs) >= 3 and segs[0] == "apk":
            app_id = f"{segs[1].lower()}/{segs[2].lower()}"
            return _ok_store("apkmirror", app_id, f"https://www.apkmirror.com/apk/{app_id}/")
        return _reject("APKMirror URL should look like /apk/<developer>/<app>/.")

    if _host_is(host, "uptodown.com"):
        labels = host.split(".")
        # <app>.<lang>.uptodown.com  or  <app>.uptodown.com
        sub = labels[:-2]
        generic = {"www", "en", "es", "fr", "de", "it", "pt", "ru", "ar", "ja", "tr", "id", "ko", "zh", "pl", "nl", "m"}
        names = [s for s in sub if s not in generic]
        if names and (not segs or segs[0] in ("android", "windows", "mac", "iphone", "linux")):
            app = names[0]
            return _ok_store("uptodown", app, f"https://{app}.en.uptodown.com/android")
        return _reject("Uptodown URL without an app subdomain (<app>.en.uptodown.com/android).")

    if _host_is(host, "aptoide.com"):
        labels = host.split(".")
        sub = [s for s in labels[:-2] if s not in {"www", "en", "es", "fr", "de", "pt", "ru", "ar", "m"}]
        if sub:
            return _ok_store("aptoide", sub[0], f"https://{sub[0]}.en.aptoide.com/app")
        if len(segs) >= 2 and segs[-2] == "app":
            return _ok_store("aptoide", segs[-1].lower(), f"https://{segs[-1].lower()}.en.aptoide.com/app")
        return _reject("Aptoide URL without an app name.")

    if _host_is(host, "apkmonk.com"):
        pkg = next((s for s in segs if _PKG.match(s)), None)
        if pkg:
            return _ok_store("apkmonk", pkg, f"https://www.apkmonk.com/app/{pkg}/")
        return _reject("APKMonk URL without a package id.")

    return _reject(f"Unsupported store host '{host}'.")


def parse_url(url: str) -> ParsedUrl:
    """Try a store listing first (unambiguous hosts), then a social profile."""
    store = parse_store_url(url)
    if store.ok or not store.reason or not store.reason.startswith("Unsupported store host"):
        return store
    return parse_profile_url(url)

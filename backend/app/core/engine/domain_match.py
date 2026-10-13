"""Domain / URL look-alike analysis (pure: no network, no database).

Two steps:

1. `normalize_url(raw)` turns whatever a person or a source supplied into a comparable form: scheme guessed,
   lower-cased, trailing dot removed, default/explicit port split off, `user@host` userinfo split off (and flagged),
   IDN converted both ways (Unicode <-> punycode `xn--`), IP hosts recognised (including decimal/hex/octal integer
   hosts), and the host split into subdomain / registrable domain / public suffix with `tldextract` using its BUNDLED
   Public Suffix List snapshot (`suffix_list_urls=()`, no cache directory): it never makes a network call.
2. `analyze_domain(...)` compares the registrable domain with the brand: official-domain exclusion by REGISTRABLE domain
   (never by substring), name look-alike (the existing `NameMatcher`, so the decided hard-negative rules apply:
   `acmeplumbing.com` is not a look-alike of Acme Bank), TLD swap, homoglyph / mixed-script IDN, brand-in-subdomain and
   `acmebank.com.evil.test` deception, `acmebank.com@evil.test` userinfo tricks, and RapidFuzz as an ADDITIONAL
   similarity signal. RapidFuzz only adds points when the home-grown matcher said `safe`, so it never double counts and
   never changes an existing verdict.

The result carries normalized values, a 0-100 domain score, the matched components and a reason per point. The score
is a domain-only score; `risk_engine.assess_web` combines it with URL reputation (algorithm 2.0).
Nothing here visits a URL.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

import idna
import tldextract
from rapidfuzz import fuzz

from .name_match import BRAND_NAME, FLAGGED, REVIEW, SAFE, NameMatcher, NameMatchResult
from .normalize import fold_text
from .scoring import Reason, name_points

WEB_MAX = {"domain_similarity": 40, "rapidfuzz_similarity": 12, "deceptive_subdomain": 20, "idn_homograph": 15, "added_words": 10}
RAPIDFUZZ_MIN_RATIO = 90.0  # only used when the home-grown matcher said `safe`
RAPIDFUZZ_MIN_LEN = 6
MAX_URL_LENGTH = 2048

_SCHEMES = ("http", "https")
_CONTROL = re.compile(r"[\x00-\x20\x7f]")


@lru_cache(maxsize=1)
def _extractor() -> tldextract.TLDExtract:
    # suffix_list_urls=() -> never download the list; cache_dir=None -> never write one. Uses the snapshot shipped with the package.
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


@dataclass(frozen=True)
class NormalizedUrl:
    ok: bool
    original: str
    reason: str | None = None  # why not ok
    scheme: str | None = None
    host: str | None = None  # ASCII (punycode) lower-case host without trailing dot or brackets
    host_unicode: str | None = None  # same host with IDN labels decoded
    port: int | None = None
    path: str = ""
    userinfo: str | None = None  # text before '@' (a credential-looking prefix is a deception trick)
    is_ip: bool = False
    ip_obfuscated: bool = False  # decimal / hex / octal integer form of an IPv4 address
    subdomain: str = ""  # ASCII
    domain_label: str = ""  # ASCII label left of the public suffix (the "name" of the site)
    suffix: str = ""  # public suffix, ASCII ("co.uk")
    registrable_domain: str | None = None  # ASCII "label.suffix"
    registrable_unicode: str | None = None
    label_unicode: str = ""
    subdomain_unicode: str = ""
    has_idn: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def canonical_url(self) -> str | None:
        """scheme://host[:port]/path with userinfo, fragment and query removed. None when not ok."""
        if not self.ok or not self.host:
            return None
        host = f"[{self.host}]" if ":" in self.host else self.host
        default = {"http": 80, "https": 443}.get(self.scheme or "")
        port = f":{self.port}" if self.port and self.port != default else ""
        return f"{self.scheme}://{host}{port}{self.path or '/'}"

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "original": self.original, "reason": self.reason, "scheme": self.scheme, "host": self.host,
            "host_unicode": self.host_unicode, "port": self.port, "path": self.path, "userinfo_present": self.userinfo is not None,
            "is_ip": self.is_ip, "ip_obfuscated": self.ip_obfuscated, "subdomain": self.subdomain, "domain_label": self.domain_label,
            "suffix": self.suffix, "registrable_domain": self.registrable_domain, "registrable_unicode": self.registrable_unicode,
            "has_idn": self.has_idn, "canonical_url": self.canonical_url, "warnings": list(self.warnings),
        }


def _bad(raw: str, reason: str) -> NormalizedUrl:
    return NormalizedUrl(False, raw, reason)


def _integer_ipv4(host: str) -> str | None:
    """`2130706433`, `0x7f000001`, `017700000001` and dotted forms with hex/octal parts are all 127.0.0.1 to a browser."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or not all(parts):
        return None
    nums: list[int] = []
    for p in parts:
        try:
            if p.startswith(("0x", "0X")):
                nums.append(int(p, 16))
            elif len(p) > 1 and p.startswith("0") and p.isdigit():
                nums.append(int(p, 8))
            elif p.isdigit():
                nums.append(int(p))
            else:
                return None
        except ValueError:
            return None
    *head, last = nums
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        return None
    value = 0
    for n in head:
        value = value * 256 + n
    value = value * 256 ** (4 - len(head)) + last
    return str(ipaddress.IPv4Address(value))


@dataclass(frozen=True)
class _Split:
    subdomain: str
    domain: str
    suffix: str
    fallback: bool = False


def _split_host(host: str) -> _Split:
    """Subdomain / domain / public suffix. An unknown TLD (`.test`, `.invalid`, a new gTLD newer than the snapshot) is
    treated as a one-label suffix so `acmebank.com.evil.test` still resolves to `evil.test`."""
    e = _extractor()(host)
    if e.suffix:
        return _Split(e.subdomain, e.domain, e.suffix)
    labels = host.split(".")
    if len(labels) >= 2 and all(labels):
        return _Split(".".join(labels[:-2]), labels[-2], labels[-1], True)
    return _Split("", e.domain or host, "")


def normalize_url(raw: str | None) -> NormalizedUrl:
    """Normalize a URL or bare host. Never raises, never touches the network."""
    original = raw if isinstance(raw, str) else ""
    text = original.strip()
    if not text:
        return _bad(original, "empty")
    if len(text) > MAX_URL_LENGTH:
        return _bad(original[:200], "too_long")
    if _CONTROL.search(text):
        return _bad(original[:200], "control_characters_or_spaces")
    text = text.replace("\\", "/")  # browsers treat a backslash like a slash
    if text.startswith("//"):
        text = "http:" + text
    elif "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        return _bad(original[:200], "unparseable")
    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        return _bad(original[:200], "unsupported_scheme")
    if not hostname:
        return _bad(original[:200], "no_host")
    userinfo = None
    netloc = parts.netloc
    if "@" in netloc:
        userinfo = netloc.rsplit("@", 1)[0]
    host = hostname.lower().rstrip(".")
    if not host:
        return _bad(original[:200], "no_host")
    warnings: list[str] = []
    path = parts.path or ""

    # IP literals
    try:
        ip = ipaddress.ip_address(host)
        return NormalizedUrl(True, original, scheme=scheme, host=str(ip), host_unicode=str(ip), port=port, path=path, userinfo=userinfo,
                             is_ip=True, warnings=("host is a raw IP address",))
    except ValueError:
        pass
    as_int = _integer_ipv4(host)
    if as_int is not None:
        return NormalizedUrl(True, original, scheme=scheme, host=as_int, host_unicode=as_int, port=port, path=path, userinfo=userinfo,
                             is_ip=True, ip_obfuscated=True, warnings=("host is an obfuscated IP address",))

    # IDN: ascii (punycode) form for comparison, unicode form for display and homoglyph checks
    try:
        ascii_host = idna.encode(host, uts46=True).decode("ascii")
    except (idna.IDNAError, UnicodeError):
        if host.isascii() and re.fullmatch(r"[a-z0-9._-]+", host) and len(host) <= 253 and ".." not in host:
            ascii_host = host  # not strictly IDNA-valid (underscore, long label) but a plain ASCII name
            warnings.append("host is not a strictly valid IDNA name")
        else:
            return _bad(original[:200], "invalid_host")
    if len(ascii_host) > 253 or any(not lb or len(lb) > 63 for lb in ascii_host.split(".")):
        return _bad(original[:200], "invalid_host")
    try:
        uni_host = idna.decode(ascii_host)
    except (idna.IDNAError, UnicodeError):
        uni_host = ascii_host
        if "xn--" in ascii_host:
            warnings.append("a punycode label could not be decoded")
    has_idn = uni_host != ascii_host or "xn--" in ascii_host

    ext_a = _split_host(ascii_host)
    ext_u = _split_host(uni_host)
    if ext_a.fallback:
        warnings.append("the top-level domain is not in the Public Suffix List snapshot: treated as a one-label suffix")
    registrable = f"{ext_a.domain}.{ext_a.suffix}" if ext_a.domain and ext_a.suffix else None
    reg_uni = f"{ext_u.domain}.{ext_u.suffix}" if ext_u.domain and ext_u.suffix else None
    ext, uni_ext = ext_a, ext_u
    if registrable is None:
        warnings.append("no public suffix: not a registrable domain")
    return NormalizedUrl(
        True, original, scheme=scheme, host=ascii_host, host_unicode=uni_host, port=port, path=path, userinfo=userinfo,
        subdomain=ext.subdomain, domain_label=ext.domain, suffix=ext.suffix, registrable_domain=registrable,
        registrable_unicode=reg_uni, label_unicode=uni_ext.domain, subdomain_unicode=uni_ext.subdomain, has_idn=has_idn,
        warnings=tuple(warnings),
    )


# ---- scripts -------------------------------------------------------------------------------------


def scripts_in(text: str) -> set[str]:
    out: set[str] = set()
    for ch in text:
        if ch.isalpha():
            try:
                out.add(unicodedata.name(ch).split(" ")[0])
            except ValueError:
                out.add("UNKNOWN")
    return out


# ---- analysis ------------------------------------------------------------------------------------


@dataclass
class DomainAnalysis:
    url: NormalizedUrl
    official: bool = False
    official_match: str | None = None  # the official registrable domain that matched
    verdict: str = "unrelated"  # official | lookalike | related | unrelated | not_comparable
    score: int = 0  # 0-100, domain signals only
    raw_total: int = 0
    reasons: list[Reason] = field(default_factory=list)
    components: dict = field(default_factory=dict)
    name_match: NameMatchResult | None = None
    brand_related: bool = False  # something tied this domain to THIS brand (the detection gate)

    def to_dict(self) -> dict:
        return {
            "url": self.url.to_dict(), "official": self.official, "official_match": self.official_match, "verdict": self.verdict,
            "score": self.score, "raw_total": self.raw_total, "reasons": [r.to_dict() for r in self.reasons],
            "components": self.components, "brand_related": self.brand_related,
        }


def official_registrables(domains: list[str]) -> dict[str, NormalizedUrl]:
    out: dict[str, NormalizedUrl] = {}
    for d in domains:
        n = normalize_url(d)
        if n.ok and n.registrable_domain:
            out.setdefault(n.registrable_domain, n)
    return out


def make_domain_matcher(brand_name: str, aliases: list[str] | tuple[str, ...], official_domains: list[str],
                        words: list[str] | None = None) -> NameMatcher:
    """NameMatcher over the brand name, its aliases and the labels of its official domains (acmebank in acmebank.in)."""
    names = [brand_name, *aliases]
    for reg in official_registrables(list(official_domains)).values():
        if reg.domain_label and not reg.domain_label.startswith("xn--") and any(c.isalpha() for c in reg.domain_label):
            names.append(reg.domain_label)
    return NameMatcher(names[0], names[1:], impersonation_words=set(words or ()))


def _folded(text: str) -> str:
    return fold_text(text) if text else ""


def _brand_in_text(matcher: NameMatcher, text: str) -> NameMatchResult | None:
    """Brand present in a subdomain / userinfo text: flagged or exact only (a `review` is too weak to call deceptive)."""
    if not text.strip():
        return None
    r = matcher.match(text)
    return r if r.verdict in (FLAGGED, BRAND_NAME) else None


def analyze_domain(
    raw_url: str,
    brand_name: str,
    official_domains: list[str] | tuple[str, ...] = (),
    *,
    aliases: list[str] | tuple[str, ...] = (),
    matcher: NameMatcher | None = None,
) -> DomainAnalysis:
    url = normalize_url(raw_url)
    if not url.ok:
        return DomainAnalysis(url, verdict="not_comparable", components={"error": url.reason})
    officials = official_registrables(list(official_domains))
    if url.is_ip:
        return DomainAnalysis(url, verdict="not_comparable", components={"note": "IP address host: there is no domain name to compare."})
    if url.registrable_domain is None:
        return DomainAnalysis(url, verdict="not_comparable", components={"note": "No public suffix: not a registrable domain."})
    if url.registrable_domain in officials:
        return DomainAnalysis(url, official=True, official_match=url.registrable_domain, verdict="official",
                              components={"note": "Registrable domain is an official domain of the brand."})

    matcher = matcher or make_domain_matcher(brand_name, list(aliases), list(officials))
    reasons: list[Reason] = []
    comp: dict = {
        "registrable_domain": url.registrable_domain, "label": url.domain_label, "subdomain": url.subdomain, "suffix": url.suffix,
        "techniques": [], "tld_swap": False, "brand_in_subdomain": False, "userinfo_trick": False, "mixed_script": False,
    }

    # -- 1. the label of the registrable domain against the brand
    label_text = url.label_unicode or url.domain_label
    name = matcher.match(label_text)
    comp["name_match"] = {"verdict": name.verdict, "pct": name.name_match_pct, "techniques": name.techniques,
                          "squat_category": name.squat_category, "added_words": name.added_words, "matched_brand": name.matched_brand}
    comp["techniques"] = list(name.techniques)
    pts, note = name_points(name, WEB_MAX["domain_similarity"])
    brand_label = _folded(brand_name)
    same_label_other_suffix = any(
        _folded(o.domain_label) == _folded(label_text) and o.suffix != url.suffix for o in officials.values()
    ) or (not officials and _folded(label_text) == brand_label)
    if same_label_other_suffix and name.verdict in (BRAND_NAME, FLAGGED):
        comp["tld_swap"] = True
        official_tlds = sorted({o.suffix for o in officials.values()})
        why = f"Same name with a different top-level domain (.{url.suffix}" + (f" instead of .{', .'.join(official_tlds)})." if official_tlds else ")." )
        note = why + " " + note if note else why
    if pts:
        reasons.append(Reason("domain_similarity", pts, WEB_MAX["domain_similarity"], note))

    # -- 2. homoglyph / mixed-script IDN
    non_ascii = any(ord(c) > 127 for c in label_text)
    scripts = scripts_in(label_text)
    comp["scripts"] = sorted(scripts)
    comp["mixed_script"] = len(scripts) > 1
    comp["punycode_label"] = url.domain_label if url.domain_label.startswith("xn--") else None
    if non_ascii:
        folds_to_brand = name.verdict in (FLAGGED, BRAND_NAME) and ("homoglyph" in name.techniques or _folded(label_text) in {_folded(t.name) for t in matcher.tokens})
        if folds_to_brand or (comp["mixed_script"] and name.verdict != SAFE):
            why = "The domain uses non-Latin look-alike characters" + (" mixed with Latin letters" if comp["mixed_script"] else "") + f" (shown as {url.registrable_domain})."
            reasons.append(Reason("idn_homograph", WEB_MAX["idn_homograph"], WEB_MAX["idn_homograph"], why))
        elif comp["mixed_script"]:
            comp["note_mixed_script"] = "Mixed scripts in the domain label, but it does not resemble the brand: not scored."

    # -- 3. brand in the subdomain, or user-info trick
    deceptive = 0
    sub_text = url.subdomain_unicode or url.subdomain
    if sub_text:
        for chunk in [sub_text, *sub_text.split(".")]:
            hit = _brand_in_text(matcher, chunk)
            if hit is not None:
                comp["brand_in_subdomain"] = True
                deceptive = WEB_MAX["deceptive_subdomain"]
                looks_domain = bool(re.search(r"\.(?:[a-z]{2,3}|co|com|org|net)(?:\.|$)", sub_text)) or "." in chunk
                comp["subdomain_looks_like_domain"] = looks_domain
                reasons.append(Reason(
                    "deceptive_subdomain", deceptive, WEB_MAX["deceptive_subdomain"],
                    f"The brand appears in the subdomain ({sub_text}) but the real registrable domain is {url.registrable_domain}"
                    + (": it is written to look like an official address." if looks_domain else "."),
                ))
                break
    if url.userinfo is not None and not deceptive:
        user = url.userinfo.split(":", 1)[0]
        hit = next((h for h in (_brand_in_text(matcher, chunk) for chunk in [user, *user.split(".")]) if h is not None), None)
        official_in_user = any(o in user.lower() for o in officials) if officials else False
        if hit is not None or official_in_user:
            comp["userinfo_trick"] = True
            deceptive = WEB_MAX["deceptive_subdomain"]
            reasons.append(Reason(
                "deceptive_subdomain", deceptive, WEB_MAX["deceptive_subdomain"],
                f"The part before '@' mentions the brand, but the browser goes to {url.registrable_domain}.",
            ))

    # -- 4. added words (only with a name match; same idea as the social scorer)
    if name.verdict in (FLAGGED, REVIEW) and name.added_words:
        strong = [w for w in name.added_words if w in matcher.strong]
        if strong:
            reasons.append(Reason("added_words", 10, WEB_MAX["added_words"], f"Added scam-flavoured words: {', '.join(strong)}."))
        elif name.impersonation_words:
            reasons.append(Reason("added_words", 6, WEB_MAX["added_words"], f"Added words often used to pose as the company: {', '.join(name.impersonation_words)}."))

    # -- 5. RapidFuzz: an additional signal, only when the home-grown matcher said `safe`
    cand = _folded(label_text)
    best = max(((_folded(t.name), t.name) for t in matcher.tokens), key=lambda p: fuzz.ratio(cand, p[0]), default=("", ""))
    ratio = float(fuzz.ratio(cand, best[0])) if best[0] else 0.0
    comp["rapidfuzz"] = {"ratio": round(ratio, 1), "partial_ratio": round(float(fuzz.partial_ratio(cand, best[0])), 1) if best[0] else 0.0,
                         "token_set_ratio": round(float(fuzz.token_set_ratio(label_text.replace("-", " "), best[1])), 1) if best[1] else 0.0,
                         "against": best[1] or None, "counted": False}
    if name.verdict == SAFE and ratio >= RAPIDFUZZ_MIN_RATIO and len(cand) >= RAPIDFUZZ_MIN_LEN and cand != best[0]:
        pts = round(WEB_MAX["rapidfuzz_similarity"] * min(1.0, (ratio - RAPIDFUZZ_MIN_RATIO) / 10 + 0.5))
        comp["rapidfuzz"]["counted"] = True
        reasons.append(Reason("rapidfuzz_similarity", pts, WEB_MAX["rapidfuzz_similarity"],
                              f"RapidFuzz string similarity {ratio:.0f}/100 to \"{best[1]}\" (secondary signal; the name matcher alone did not flag it)."))

    raw = sum(r.points for r in reasons)
    score = min(100, raw)
    related = bool(reasons) and any(r.points > 0 for r in reasons) or name.verdict != SAFE
    verdict = "lookalike" if (name.verdict in (FLAGGED, BRAND_NAME) or deceptive or any(r.signal == "idn_homograph" for r in reasons)) else (
        "related" if related else "unrelated")
    return DomainAnalysis(url, verdict=verdict, score=score, raw_total=raw, reasons=reasons, components=comp, name_match=name, brand_related=related)

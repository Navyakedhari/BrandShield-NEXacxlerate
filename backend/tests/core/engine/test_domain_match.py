"""Domain / URL analysis: normalization edge cases, official-domain exclusion, look-alike techniques. No network (tldextract uses its snapshot)."""

from __future__ import annotations

import json
import socket

import pytest

from app.core.engine import domain_match as dm
from app.core.engine.domain_match import analyze_domain, normalize_url

OFFICIAL = ["acmebank.in"]
CYR_A = "а"  # Cyrillic small a, looks like Latin a


def an(url: str, official=OFFICIAL, brand="Acme Bank"):
    return analyze_domain(url, brand, official)


def signals(a):
    return {r.signal: r.points for r in a.reasons}


# ---- normalization -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, host, registrable",
    [
        ("HTTPS://WWW.AcmeBank.IN/Login?x=1#frag", "www.acmebank.in", "acmebank.in"),
        ("acmebank.in", "acmebank.in", "acmebank.in"),  # no scheme
        ("//acmebank.in/path", "acmebank.in", "acmebank.in"),  # scheme-relative
        ("https://acmebank.in./", "acmebank.in", "acmebank.in"),  # trailing dot
        ("https://acmebank.in:8443/a", "acmebank.in", "acmebank.in"),  # port
        ("https://login.secure.acmebank.co.uk/", "login.secure.acmebank.co.uk", "acmebank.co.uk"),  # multi-part suffix
        ("https://user:pass@acmebank.in/", "acmebank.in", "acmebank.in"),  # userinfo is not the host
        ("https:\\\\acmebank.in\\x", "acmebank.in", "acmebank.in"),  # backslashes are slashes to a browser
        ("https://evil.test", "evil.test", "evil.test"),  # unknown TLD is treated as a one-label suffix
    ],
)
def test_normalize_hosts(raw, host, registrable):
    n = normalize_url(raw)
    assert n.ok and n.host == host and n.registrable_domain == registrable and not n.is_ip


def test_normalize_port_and_canonical_url():
    n = normalize_url("HTTPS://Acme.Test:8443/Path/x?q=1#f")
    assert n.port == 8443 and n.canonical_url == "https://acme.test:8443/Path/x"
    assert normalize_url("http://acme.test:80/").canonical_url == "http://acme.test/"  # default port dropped
    assert normalize_url("https://a:b@acme.test/").canonical_url == "https://acme.test/"  # no credentials in the canonical form


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "ftp://acmebank.in", "javascript:alert(1)", "https://", "bad host.com", "http://exa mple.com", "https://acme..bank.com",
     "https://acmebank.in:99999/", "x" * 3000],
)
def test_normalize_rejects(raw):
    assert not normalize_url(raw).ok


@pytest.mark.parametrize("raw", ["http://127.0.0.1/", "http://[::1]:8080/", "http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/", "192.168.0.1"])
def test_ip_hosts_are_recognised_and_not_compared(raw):
    n = normalize_url(raw)
    assert n.ok and n.is_ip and n.registrable_domain is None
    a = an(raw)
    assert a.verdict == "not_comparable" and a.score == 0 and not a.brand_related


def test_integer_ip_is_flagged_as_obfuscated():
    n = normalize_url("http://2130706433/")
    assert n.ip_obfuscated and n.host == "127.0.0.1"


def test_single_label_host_has_no_registrable_domain():
    n = normalize_url("http://localhost:3000/")
    assert n.ok and n.registrable_domain is None and an("localhost").verdict == "not_comparable"


# ---- IDN / punycode ------------------------------------------------------------------------------


def test_idn_round_trip_unicode_and_punycode():
    uni = normalize_url(f"https://{CYR_A}cmebank.com/")
    assert uni.has_idn and uni.host.startswith("xn--") and uni.host_unicode == f"{CYR_A}cmebank.com"
    again = normalize_url("https://" + uni.host)
    assert again.host == uni.host and again.host_unicode == f"{CYR_A}cmebank.com" and again.has_idn


def test_cyrillic_homograph_of_the_brand_is_flagged():
    a = an(f"https://{CYR_A}cmebank.com/")
    assert a.verdict == "lookalike" and "idn_homograph" in signals(a) and a.components["mixed_script"] is True
    assert a.components["punycode_label"].startswith("xn--")


def test_punycode_input_is_decoded_for_the_comparison():
    puny = normalize_url(f"https://{CYR_A}cmebank.com/").host
    a = an("http://" + puny)
    assert a.verdict == "lookalike" and "idn_homograph" in signals(a)


def test_homograph_in_the_middle_of_the_name():
    assert "idn_homograph" in signals(an(f"https://acmeb{CYR_A}nk.com"))
    assert an("https://acmebank.com").components["mixed_script"] is False


def test_mixed_script_that_does_not_resemble_the_brand_is_not_scored():
    a = an(f"https://p{CYR_A}ypal-fan.com")
    assert a.score == 0 and not a.brand_related


def test_broken_punycode_does_not_raise():
    n = normalize_url("https://xn--.com")
    assert isinstance(n.ok, bool)
    assert isinstance(normalize_url("https://xn--zzzzzzzzzzzzzzzzzzzzzzzz.com").ok, bool)


# ---- official domains are never flagged -----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["https://acmebank.in", "https://www.acmebank.in/login", "https://ACMEBANK.IN./", "https://a.b.c.acmebank.in:8443/x", "https://user@acmebank.in/", "//acmebank.in"],
)
def test_official_domain_and_its_subdomains_are_official(url):
    a = an(url)
    assert a.official and a.verdict == "official" and a.score == 0 and a.official_match == "acmebank.in"


def test_official_check_is_by_registrable_domain_not_substring():
    assert not an("https://acmebank.in.evil.test").official
    assert not an("https://notacmebank.in").official
    assert not an("https://acmebank.in-secure.com").official


# ---- look-alike techniques -----------------------------------------------------------------------


def test_tld_swap():
    a = an("https://acmebank.com/")
    assert a.verdict == "lookalike" and a.components["tld_swap"] is True and signals(a)["domain_similarity"] >= 30
    assert "different top-level domain" in a.reasons[0].note
    b = an("https://acmebank.co.uk/")
    assert b.components["tld_swap"] is True and b.url.registrable_domain == "acmebank.co.uk"


def test_typo_and_combo_domains():
    assert an("https://acmebanc.com").verdict == "lookalike"
    assert an("https://acmebnk.com").verdict == "lookalike"
    a = an("https://acmebank-support.com")
    assert a.verdict == "lookalike" and "added_words" in signals(a)


@pytest.mark.parametrize(
    "url",
    ["https://acmebank.com.evil.test", "https://login.acmebank.evil.test/", "https://acmebank.in.evil.test",
     "https://secure.acmebank.in.verify.example.org/x", "https://acmebank.github.io"],
)
def test_deceptive_subdomains(url):
    a = an(url)
    assert a.verdict == "lookalike" and signals(a).get("deceptive_subdomain") == 20 and a.components["brand_in_subdomain"] is True
    assert a.url.registrable_domain != "acmebank.in"
    assert "real registrable domain" in next(r.note for r in a.reasons if r.signal == "deceptive_subdomain")


def test_user_at_host_trick():
    a = an("https://acmebank.in@evil.test/login")
    assert a.url.registrable_domain == "evil.test" and a.url.userinfo == "acmebank.in"
    assert a.components["userinfo_trick"] is True and signals(a)["deceptive_subdomain"] == 20
    b = an("https://acmebank.com:secret@evil.test/")
    assert b.components["userinfo_trick"] is True
    c = an("https://bob@evil.test/")  # user info that has nothing to do with the brand
    assert c.score == 0 and c.components["userinfo_trick"] is False


def test_the_host_decides_not_the_text_before_the_at_sign():
    assert an("https://login.acmebank.in@evil.test").official is False
    assert an("https://evil.test@login.acmebank.in/").official is True


# ---- hard negatives: nothing flagged ---------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["https://acmeplumbing.com", "https://acmebakery.net", "https://example.org", "https://google.com", "https://bankofsomewhere.com",
     "https://acme-hotels.test", "https://weather.acmeweather.com"],
)
def test_hard_negatives_are_not_flagged(url):
    a = an(url)
    assert a.score == 0 and not a.brand_related and a.verdict == "unrelated", (url, signals(a))


# ---- RapidFuzz ---------------------------------------------------------------------------------------


def test_rapidfuzz_is_recorded_and_never_double_counts():
    a = an("https://acmebank.com")
    assert a.components["rapidfuzz"]["ratio"] == 100.0 and a.components["rapidfuzz"]["counted"] is False
    assert "rapidfuzz_similarity" not in signals(a)
    assert 0 <= an("https://acmeplumbing.com").components["rapidfuzz"]["ratio"] < 90
    for u in ("https://acmebankk.com", "https://acmebanck.com", "https://acmebank-online.com"):
        r = an(u)
        # RapidFuzz points exist only when the name matcher said `safe`
        if "rapidfuzz_similarity" in signals(r):
            assert r.name_match.verdict == "safe"


def test_rapidfuzz_never_overrides_the_acme_bakery_rule():
    assert an("https://acmebakery.com").score == 0


# ---- no network -------------------------------------------------------------------------------------


def test_analysis_makes_no_network_call(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    dm._extractor.cache_clear()
    for u in ("https://acmebank.com", "https://acmebank.com.evil.test", f"https://{CYR_A}cmebank.com"):
        an(u)
    assert tuple(dm._extractor().suffix_list_urls) == ()


def test_to_dict_is_json_serialisable():
    json.dumps(an("https://acmebank.com.evil.test").to_dict())

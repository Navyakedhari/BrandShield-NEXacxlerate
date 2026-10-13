from datetime import UTC, datetime

import pytest

from app.core.collectors.base import (
    BrandQuery,
    Candidate,
    SourceUnavailable,
    candidate_from_url,
    clean_title,
    dedupe,
    parse_retry_after,
)
from app.core.collectors.sample import SampleCollector
from app.core.collectors.variants import generate_variants
from app.core.engine.allowlist import Allowlist
from app.core.engine.models import BrandProfileInput, OfficialAssets
from app.core.engine.pipeline import evaluate_app_candidate, evaluate_social_candidate

from .helpers import BRAND, NOW, ctx


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Acme Bank (@acmebank) • Instagram photos and videos", "Acme Bank"),
        ("Acme Bank | Instagram", "Acme Bank"),
        ("Acme Bank - Home | Facebook", "Acme Bank"),
        ("Acme Bank (@acmebank) / X", "Acme Bank"),
        ("Acme Bank on X: \"hello world\"", "Acme Bank"),
        ("Acme Bank: Overview | LinkedIn", "Acme Bank"),
        ("Acme Bank – Telegram", "Acme Bank"),
        ("Acme Bank - YouTube", "Acme Bank"),
        ("Acme Bank - Apps on Google Play", "Acme Bank"),
        ("Acme Bank on the App Store", "Acme Bank"),
        ("Acme Bank MOD APK 9.9 Download - APKPure.com", "Acme Bank"),
        ("Plain Name", "Plain Name"),
        ("   ", None),
        (None, None),
        ("", None),
    ],
)
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


@pytest.mark.parametrize(
    "url,platform,kind,handle",
    [
        ("https://www.instagram.com/acme_x/?hl=en", "instagram", "social", "acme_x"),
        ("https://twitter.com/Acme_X", "x", "social", "acme_x"),
        ("https://t.me/s/acmechannel", "telegram", "social", "acmechannel"),
        ("https://play.google.com/store/apps/details?id=com.a.b&hl=en", "google_play", "app", "com.a.b"),
        ("https://apps.apple.com/in/app/acme/id1234567890", "app_store", "app", "1234567890"),
        ("https://apkpure.com/acme/com.acme.app", "third_party_apk", "app", "com.acme.app"),
    ],
)
def test_candidate_from_url(url, platform, kind, handle):
    c = candidate_from_url(url, source="tavily", fetched_at=NOW)
    assert (c.platform, c.kind, c.handle) == (platform, kind, handle)


@pytest.mark.parametrize(
    "url",
    ["https://www.instagram.com/p/abc/", "https://www.instagram.com/explore/", "https://example.com/acme", "", None, "not a url",
     "https://www.facebook.com/groups/123456", "https://x.com/search", "javascript:alert(1)", "https://youtu.be/abc"],
)
def test_non_profile_urls_are_dropped(url):
    assert candidate_from_url(url, source="tavily") is None


def test_candidate_from_url_respects_allowed_platforms():
    assert candidate_from_url("https://x.com/acme", source="s", allowed_platforms=frozenset({"instagram"})) is None
    assert candidate_from_url("https://x.com/acme", source="s", allowed_platforms=frozenset({"x"})) is not None


def test_dedupe_merges_and_fills_missing_fields():
    a = Candidate("x", "social", "Acme", None, "u", "t")
    b = Candidate("x", "social", "acme", "Name", "u", "t", bio="bio")
    out = dedupe([a, b, Candidate("x", "social", None, None, None, "t")])
    assert len(out) == 1 and out[0].display_name == "Name" and out[0].bio == "bio"


def test_source_unavailable_fields_and_retry_after_parsing():
    e = SourceUnavailable("quota", "limit", 30)
    assert (e.reason, e.message, e.retry_after) == ("quota", "limit", 30)
    assert str(SourceUnavailable("no_key")) == "no_key"
    assert parse_retry_after("12") == 12.0 and parse_retry_after("abc") is None and parse_retry_after(None) is None
    assert parse_retry_after("Tue, 13 Oct 2026 12:01:00 GMT", NOW) == 60.0
    assert parse_retry_after("999999999") == 7 * 86400


def test_variants_deterministic_ascii_distinct():
    v = generate_variants("Acme Bank", limit=6)
    assert v == generate_variants("Acme Bank", limit=6) and len(v) == len(set(x.lower() for x in v)) and 1 < len(v) <= 6
    assert all(x.isascii() and x.lower() != "acme bank" for x in v)
    assert generate_variants("ab") == [] and generate_variants("Acme", limit=0) == []


# ---------------------------------------------------------------------------------------- sample


def _sample(brand=BRAND, now=NOW, **kw):
    return SampleCollector(**kw).collect(brand, ctx(now=lambda: now))


def test_sample_is_deterministic_for_same_brand_and_differs_for_another():
    a, b = _sample(), _sample()
    assert [c.to_dict() for c in a] == [c.to_dict() for c in b]
    other = BrandQuery(brand_id=2, name="Zenith Pay")
    assert [c.handle for c in _sample(other)] != [c.handle for c in a]


def test_every_sample_candidate_is_marked_sample():
    out = _sample()
    assert out and all(c.source == "sample" and c.data_source == "sample" and c.extras["data_source"] == "sample" for c in out)
    assert all(c.fetched_at == NOW for c in out)


def test_sample_contains_echoes_lookalikes_and_hard_negatives():
    out = _sample()
    roles = {c.extras["sample_role"] for c in out}
    assert roles == {"official_echo", "lookalike", "hard_negative"}
    echoes = [c for c in out if c.extras["sample_role"] == "official_echo"]
    assert {(c.platform, c.handle) for c in echoes} == {("instagram", "acmebank"), ("x", "acmebank"), ("google_play", "com.acmebank.mobile")}
    looks = [c for c in out if c.extras["sample_role"] == "lookalike" and c.kind == "social"]
    assert len(looks) >= 10 and all((c.bio or "") for c in looks)
    assert {c.extras["sample_tier"] for c in looks} == {"strong", "medium", "weak"}
    assert all(c.extras["account_age_days"] <= 60 for c in looks if c.extras["sample_tier"] != "weak")
    assert all(c.handle != "acmebank" for c in looks)
    negs = [c for c in out if c.extras["sample_role"] == "hard_negative"]
    assert negs and all(c.display_name.startswith("Acme ") for c in negs) and all(c.extras["account_age_days"] > 365 for c in negs)
    assert any(c.kind == "app" and c.extras["sample_role"] == "lookalike" for c in out)
    for c in out:  # urls parse back to the same platform and handle
        if c.kind == "social":
            from app.core.engine.urls import parse_profile_url

            p = parse_profile_url(c.profile_url)
            assert p.ok and p.platform == c.platform and p.handle == c.handle.lower()


def test_sample_engine_proof_official_excluded_lookalikes_flagged_negatives_not():
    out = _sample()
    brand = BrandProfileInput(name="Acme Bank", aliases=["Acme"])
    official = OfficialAssets(social_handles={"instagram": ["acmebank"], "x": ["acmebank"]}, play_package_ids=["com.acmebank.mobile"])
    al = Allowlist(official)
    today = NOW.date()
    flagged_looks = 0
    for c in out:
        role = c.extras["sample_role"]
        eng = c.to_engine()
        det = (
            evaluate_social_candidate(brand, official, eng, today=today, allowlist=al)
            if c.kind == "social"
            else evaluate_app_candidate(brand, official, eng, today=today, allowlist=al)
        )
        if role == "official_echo":
            assert det is None, "official echoes must be excluded by the allowlist"
        elif role == "hard_negative":
            assert det is not None and det.name_match.verdict != "flagged", c.display_name
        elif det is not None and det.name_match.verdict == "flagged":
            flagged_looks += 1
    assert flagged_looks >= 3


def test_sample_without_official_assets_invents_no_echoes_and_respects_platforms():
    out = _sample(BrandQuery(brand_id=5, name="Zenith Pay"))
    assert out and not [c for c in out if c.extras["sample_role"] == "official_echo"]
    only = SampleCollector().collect(BRAND, ctx(platforms=frozenset({"instagram"})))
    assert only and {c.platform for c in only} == {"instagram"}


def test_candidate_roundtrip_and_engine_conversion():
    c = _sample()[0]
    again = Candidate.from_dict(c.to_dict())
    assert again.to_dict() == c.to_dict()
    assert datetime.now(UTC).tzinfo is not None

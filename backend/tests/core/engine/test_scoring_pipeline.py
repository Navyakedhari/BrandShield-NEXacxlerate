from datetime import date

import pytest

from app.core.engine.allowlist import Allowlist
from app.core.engine.llm import LLMIntentResult
from app.core.engine.models import BrandProfileInput, CandidateApp, CandidateSocial, OfficialAssets
from app.core.engine.name_match import match_name
from app.core.engine.pipeline import evaluate_app_candidate, evaluate_social_candidate
from app.core.engine.scoring import APP_MAX, SOCIAL_MAX, band_for, score_social
from app.core.pipeline.bands import band_for as service_band_for

TODAY = date(2026, 10, 13)
BRAND = BrandProfileInput(
    name="Acme Bank",
    description="Acme Bank offers savings accounts, home loans and credit cards. Visit your nearest branch or use our mobile banking app.",
)
OFFICIAL = OfficialAssets(
    social_handles={"instagram": ["acmebank"], "x": ["@AcmeBank"], "telegram": ["acmebank_official"]},
    social_urls=["https://www.youtube.com/@AcmeBankIndia"],
    play_package_ids=["com.acmebank.app"],
    apple_app_ids=["123456789"],
    domains=["acmebank.com"],
    publisher_names=["Acme Bank Ltd"],
)


def reasons_sum(r):
    return sum(x.points for x in r.reasons)


def test_bands_match_phase1_service():
    for s in (0, 50, 69, 70, 84, 85, 100):
        assert band_for(s) == service_band_for(s)
    assert (band_for(69), band_for(70), band_for(84), band_for(85)) == ("low", "medium", "medium", "high")


def scammy_social(**kw):
    base = dict(
        platform="instagram",
        handle="acmebank_support",
        display_name="Acme Bank Support",
        bio="Official support. Complete KYC now or account blocked. DM us on WhatsApp. Pay small processing fee. bit.ly/x",
        followers=12,
        following=900,
        posts=2,
        created_at=date(2026, 10, 5),
        verified=False,
        bio_links=["https://bit.ly/abc"],
        logo_similarity=93.0,
    )
    base.update(kw)
    return CandidateSocial(**base)


def test_social_worst_case_caps_at_100_and_reasons_sum_to_raw():
    det = evaluate_social_candidate(BRAND, OFFICIAL, scammy_social(), today=TODAY)
    s = det.score
    assert s.score == 100 and s.band == "high" and s.capped
    assert s.raw_total > 100 and reasons_sum(s) == s.raw_total
    by = {r.signal: r for r in s.reasons}
    assert by["name_similarity"].max_points == SOCIAL_MAX["name"] and by["name_similarity"].points <= 34
    assert by["logo_similarity"].points <= 28 and by["added_words"].points == 12
    assert by["bio_signals"].points == 28 and "capped" in by["bio_signals"].note
    assert all(r.note and r.points <= r.max_points for r in s.reasons)
    assert det.status == "pending_review"


def test_social_category_maxima_never_exceeded():
    det = evaluate_social_candidate(BRAND, OFFICIAL, scammy_social(), today=TODAY)
    account = sum(r.points for r in det.score.reasons if r.signal not in ("name_similarity", "logo_similarity", "added_words", "bio_signals"))
    assert account <= SOCIAL_MAX["account"]
    assert sum(SOCIAL_MAX.values()) == 120


def test_social_name_only_is_low_and_explainable():
    c = CandidateSocial(platform="instagram", handle="acm3bank", display_name="acm3bank", bio="Welcome", verified=None)
    det = evaluate_social_candidate(BRAND, OFFICIAL, c, today=TODAY)
    assert det.score.band == "low" and 0 < det.score.score <= 34
    assert [r.signal for r in det.score.reasons] == ["name_similarity"]
    assert det.name_match.techniques == ["digit_or_symbol_swap"]


def test_not_overflagging_unrelated_businesses():
    for name in ("Acme Bakery", "Acme Hotels", "Acme Weather"):
        c = CandidateSocial(platform="instagram", handle=name.replace(" ", "").lower(), display_name=name, bio="Fresh and local", followers=5000, posts=300, verified=False)
        det = evaluate_social_candidate(BRAND, OFFICIAL, c, today=TODAY)
        assert det.name_match.verdict in ("safe", "review") and det.score.band == "low"
        assert det.score.score < 20


def test_exact_brand_name_with_non_official_handle_is_scored_not_dropped():
    c = CandidateSocial(platform="instagram", handle="rahul_k_99", display_name="Acme Bank", bio="", verified=False)
    det = evaluate_social_candidate(BRAND, OFFICIAL, c, today=TODAY)
    assert det is not None  # name alone never excludes: the exact brand name can be an impersonator
    assert det.name_match.verdict == "brand_name" and det.matched_text == "Acme Bank"
    assert det.score.reasons[0].signal == "name_similarity"


def test_allowlist_official_assets_never_produce_a_result():
    for c in (
        CandidateSocial(platform="instagram", handle="@AcmeBank"),
        CandidateSocial(platform="instagram", handle="acmebank", display_name="totally different name"),
        CandidateSocial(platform="x", handle="acmebank"),
        CandidateSocial(platform="twitter", handle="ACMEBANK"),
        CandidateSocial(platform="instagram", url="https://m.instagram.com/acmebank/?hl=en"),
        CandidateSocial(platform="x", url="https://twitter.com/AcmeBank"),
        CandidateSocial(platform="telegram", handle="acmebank_official"),
        CandidateSocial(platform="youtube", url="https://www.youtube.com/@acmebankindia"),
    ):
        assert evaluate_social_candidate(BRAND, OFFICIAL, c) is None, c
    for a in (
        CandidateApp(store="google_play", app_id="com.acmebank.app", title="Fake name"),
        CandidateApp(store="google_play", url="https://play.google.com/store/apps/details?id=com.AcmeBank.app&hl=en"),
        CandidateApp(store="apple_app_store", app_id="123456789"),
        CandidateApp(store="apple_app_store", url="https://apps.apple.com/in/app/acme-bank/id123456789"),
    ):
        assert evaluate_app_candidate(BRAND, OFFICIAL, a) is None, a


def test_allowlist_is_exact_not_by_name_or_other_platform():
    al = Allowlist(OFFICIAL)
    assert al.check_social(CandidateSocial(platform="instagram", handle="acmebank")).asset == "handle"
    assert al.check_social(CandidateSocial(platform="instagram", url="https://instagram.com/acmebank")).asset == "url"
    assert al.check_social(CandidateSocial(platform="instagram", handle="acmebank.official")) is None
    assert al.check_social(CandidateSocial(platform="instagram", handle="acmebank_official")) is None  # telegram-only handle
    assert al.check_social(CandidateSocial(platform="facebook", handle="acmebank")) is None
    assert al.check_social(CandidateSocial(platform="instagram", display_name="Acme Bank")) is None
    # a mirror listing of the official package is a third-party copy, not official
    assert al.check_app(CandidateApp(store="apkpure", app_id="com.acmebank.app")) is None
    assert al.check_app(CandidateApp(store="google_play", app_id="com.acmebank.app.lite")) is None


def test_llm_points_inside_bio_cap():
    c = CandidateSocial(platform="instagram", handle="acmebank_help", display_name="Acme Bank Help", bio="send us your details")
    name = match_name(c.handle, BRAND.name)
    without = score_social(c, OFFICIAL, name, today=TODAY)
    with_llm = score_social(c, OFFICIAL, name, today=TODAY, llm=LLMIntentResult(0.9, ["kyc_lure"], "looks like a lure"))
    assert with_llm.raw_total - without.raw_total == 9
    low = score_social(c, OFFICIAL, name, today=TODAY, llm=LLMIntentResult(0.3))
    assert low.raw_total == without.raw_total


def test_account_signals_individually():
    c = CandidateSocial(platform="instagram", handle="zzz", created_at=date(2026, 9, 20), posts=0, followers=3, following=800, verified=False, bio="", bio_links=["https://evil.example/x"])
    s = score_social(c, OFFICIAL, match_name("zzz", "Acme Bank"), today=TODAY)
    got = {r.signal: r.points for r in s.reasons}
    assert got == {"account_age": 4, "few_posts": 3, "follower_oddity": 4, "unverified": 2, "empty_bio": 1, "link_not_official": 2}
    old = CandidateSocial(platform="instagram", handle="zzz", created_at=date(2015, 1, 1), posts=900, followers=9000, following=10, verified=True, bio="x")
    assert score_social(old, OFFICIAL, match_name("zzz", "Acme Bank"), today=TODAY).score == 0


def test_logo_points_scale_and_threshold():
    name = match_name("zzz", "Acme Bank")

    def pts(sim):
        c = CandidateSocial(platform="x", handle="zzz", logo_similarity=sim)
        s = score_social(c, OFFICIAL, name, today=TODAY)
        return next((r.points for r in s.reasons if r.signal == "logo_similarity"), 0)

    assert pts(None) == 0 and pts(40) == 0 and pts(54.9) == 0
    assert 0 < pts(55) < pts(70) < pts(80) < pts(90) == 28 == pts(100)


# ---- apps -------------------------------------------------------------------------------------


def test_app_publisher_points_only_with_another_brand_signal():
    fake_pub = CandidateApp(store="google_play", app_id="com.other.thing", title="Super Photo Editor", publisher="Pixel Studio Pvt Ltd", installs=5000000)
    s = evaluate_app_candidate(BRAND, OFFICIAL, fake_pub, today=TODAY).score
    pub = next(r for r in s.reasons if r.signal == "publisher_differs")
    assert pub.points == 0 and "not counted" in pub.note and s.score == 0

    with_name = CandidateApp(store="google_play", app_id="com.fake.acmebank", title="Acme Bank Rewards", publisher="Pixel Studio Pvt Ltd", installs=5000000)
    s2 = evaluate_app_candidate(BRAND, OFFICIAL, with_name, today=TODAY).score
    pub2 = next(r for r in s2.reasons if r.signal == "publisher_differs")
    assert pub2.points == 20 and pub2.max_points == 22


def test_app_publisher_claiming_brand_gets_22_and_official_publisher_zero():
    c = CandidateApp(store="google_play", app_id="com.x.y", title="Acme Bank Support", publisher="Acme Bank Services")
    s = evaluate_app_candidate(BRAND, OFFICIAL, c, today=TODAY).score
    assert next(r.points for r in s.reasons if r.signal == "publisher_differs") == 22
    same = CandidateApp(store="google_play", app_id="com.x.y", title="Acme Bank Support", publisher="ACME BANK LIMITED")
    s = evaluate_app_candidate(BRAND, OFFICIAL, same, today=TODAY).score
    assert not any(r.signal == "publisher_differs" for r in s.reasons)


def test_app_without_known_official_publisher_cannot_compare():
    off = OFFICIAL.model_copy(update={"publisher_names": []})
    c = CandidateApp(store="google_play", app_id="com.x.y", title="Acme Bank Support", publisher="Someone")
    s = evaluate_app_candidate(BRAND, off, c, today=TODAY).score
    pub = next(r for r in s.reasons if r.signal == "publisher_differs")
    assert pub.points == 0 and "cannot be compared" in pub.note


def test_app_full_score_caps_and_sums():
    c = CandidateApp(
        store="apkpure",
        app_id="com.acmebank.app.mod",
        title="Acme Bank Support",
        publisher="Acme Bank Services",
        description=BRAND.description,
        icon_similarity=95,
        installs=40,
        rating_count=2,
        published_at=date(2026, 10, 1),
    )
    det = evaluate_app_candidate(BRAND, OFFICIAL, c, today=TODAY)
    s = det.score
    assert s.score == 100 and s.capped and s.raw_total == reasons_sum(s) > 100
    by = {r.signal: r for r in s.reasons}
    assert by["icon_similarity"].points == 30 and by["description_overlap"].points == 17
    assert by["publisher_differs"].points == 22
    store = sum(r.points for r in s.reasons if r.signal in ("third_party_mirror", "few_installs", "new_listing", "few_ratings"))
    assert store == 10
    assert sum(APP_MAX.values()) == 103


def test_app_package_id_used_as_name_signal():
    c = CandidateApp(store="google_play", app_id="com.acmebank.support", title="Helper Tool")
    det = evaluate_app_candidate(BRAND, OFFICIAL, c, today=TODAY)
    assert det.name_match.verdict == "flagged" and det.matched_text == "acmebank.support"


def test_app_by_url_only():
    c = CandidateApp(store="google_play", url="https://play.google.com/store/apps/details?id=com.acmebank.help", title="Tool")
    assert evaluate_app_candidate(BRAND, OFFICIAL, c, today=TODAY).name_match.verdict == "flagged"


def test_detection_to_dict_and_reason_shape():
    det = evaluate_social_candidate(BRAND, OFFICIAL, scammy_social(), today=TODAY)
    d = det.to_dict()
    assert d["band"] == "high" and d["status"] == "pending_review"
    assert set(d["reasons"][0]) == {"signal", "points", "max_points", "note"}


@pytest.mark.parametrize("pct_case", ["acmebank_support", "acmebnak", "acme-bank", "acmebank123"])
def test_flagged_names_always_yield_points(pct_case):
    c = CandidateSocial(platform="instagram", handle=pct_case)
    det = evaluate_social_candidate(BRAND, OFFICIAL, c, today=TODAY)
    assert det.score.score >= 20

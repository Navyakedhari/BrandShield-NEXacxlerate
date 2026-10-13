import pytest

from app.core.engine.name_match import (
    ADDED_WORD,
    BRAND_NAME,
    CHARACTER_SWAP,
    COMBO,
    DIGIT_OR_SYMBOL_SWAP,
    EXACT,
    FLAGGED,
    FUZZY,
    HOMOGLYPH,
    MISSING_OR_EXTRA_LETTER,
    NONE,
    REVIEW,
    SAFE,
    SPACING_OR_SEPARATOR,
    TRANSPOSED,
    TYPO,
    NameMatcher,
    match_name,
)

BRAND = "Acme Bank"


@pytest.mark.parametrize(
    "candidate",
    ["acrnebank", "Acme Bank Support", "acme-bank", "A C M E Bank", "acm3bank", "Acme Bnak", "acmebank_official"],
)
def test_required_flagged(candidate):
    r = match_name(candidate, BRAND)
    assert r.verdict == FLAGGED, r
    assert r.name_match_pct >= 80


@pytest.mark.parametrize("candidate", ["Acme Bakery", "Acme Hotels", "Acme Weather"])
def test_required_not_flagged(candidate):
    r = match_name(candidate, BRAND)
    assert r.verdict in (SAFE, REVIEW)
    assert r.verdict != FLAGGED
    assert r.name_match_pct < 60


def test_exact_name_is_brand_name_not_flagged():
    for c in ("Acme Bank", "acmebank", "ACME BANK", "@acmebank", "  acme   bank "):
        r = match_name(c, BRAND)
        assert r.verdict == BRAND_NAME and r.name_match_pct == 100 and r.squat_category == EXACT


@pytest.mark.parametrize(
    "candidate,technique",
    [
        ("acmebnak", TRANSPOSED),
        ("acmebank_", SPACING_OR_SEPARATOR),
        ("acme.bank", SPACING_OR_SEPARATOR),
        ("acme_bank", SPACING_OR_SEPARATOR),
        ("a c m e bank", SPACING_OR_SEPARATOR),
        ("acm e bank", SPACING_OR_SEPARATOR),
        ("acmebonk", CHARACTER_SWAP),
        ("acmbank", MISSING_OR_EXTRA_LETTER),
        ("acmeebank", MISSING_OR_EXTRA_LETTER),
        ("acmebanks", MISSING_OR_EXTRA_LETTER),
        ("acme8ank", DIGIT_OR_SYMBOL_SWAP),
        ("acm3bank", DIGIT_OR_SYMBOL_SWAP),
        ("4cmebank", DIGIT_OR_SYMBOL_SWAP),
        ("acme-bank-support", ADDED_WORD),
        ("аcmebank", HOMOGLYPH),  # Cyrillic a
        ("acmebаnk", HOMOGLYPH),
        ("ａｃｍｅｂａｎｋ", HOMOGLYPH),  # fullwidth
        ("ácmebank", HOMOGLYPH),  # accent
        ("acrnebank", HOMOGLYPH),  # rn -> m
        ("αcmebank", HOMOGLYPH),  # Greek alpha
    ],
)
def test_technique_reported(candidate, technique):
    r = match_name(candidate, BRAND)
    assert r.verdict == FLAGGED, r
    assert technique in r.techniques, r.techniques


def test_spacing_not_reported_for_plain_brand_spacing():
    r = match_name("Acme Bank Support", BRAND)
    assert r.techniques == [ADDED_WORD]
    assert match_name("acme bank official", BRAND).techniques == [ADDED_WORD]
    assert match_name("acme  bank", BRAND).verdict == BRAND_NAME  # double space collapses to one


def test_one_can_be_i_or_l():
    assert match_name("orb1tpay", "Orbit Pay").verdict == FLAGGED  # 1 read as i
    assert match_name("1ndigo", "Indigo").verdict == FLAGGED
    assert match_name("a1ph4", "Alpha").verdict == FLAGGED  # 1 read as l


def test_squat_categories():
    assert match_name("acmebnak", BRAND).squat_category == TYPO
    assert match_name("acmebanks", BRAND).squat_category == TYPO
    assert match_name("acm3bank", BRAND).squat_category == TYPO
    assert match_name("Acme Bank Support", BRAND).squat_category == COMBO
    assert match_name("acmebank123", BRAND).squat_category == COMBO
    assert match_name("Acme Bank", BRAND).squat_category == EXACT
    assert match_name("Acme Bake", BRAND).squat_category == FUZZY  # distance 2, review
    assert match_name("Acme Bakery", BRAND).squat_category == NONE


def test_extra_word_kind_and_added_words():
    r = match_name("acmebank123", BRAND)
    assert (r.extra_word_kind, r.added_words) == ("digits", ["123"])
    r = match_name("acmebank_support_team", BRAND)
    assert r.extra_word_kind == "dictionary" and r.added_words == ["support", "team"]
    assert r.impersonation_words == ["support", "team"]
    r = match_name("acmebankxqzv", BRAND)
    assert r.extra_word_kind == "other" and r.verdict == REVIEW
    assert match_name("customer.care.acmebank", BRAND).added_words == ["customer", "care"]
    r = match_name("acmebank_24x7", BRAND)
    assert r.extra_word_kind == "digits" and r.verdict == FLAGGED
    r = match_name("acmebank_supp0rt", BRAND)  # leet inside the added word
    assert r.verdict == FLAGGED and r.added_words == ["support"]


def test_dp_glue_words_without_separators():
    r = match_name("officialacmebankhelpteam", BRAND)
    assert r.verdict == FLAGGED
    assert r.added_words == ["official", "help", "team"]


def test_fuzzy_is_review_only_and_long_brands_only():
    r = match_name("acmbnk", BRAND)
    assert r.verdict == REVIEW and r.squat_category == FUZZY and r.edit_distance == 2
    assert match_name("zxho", "Zoho").verdict == SAFE  # short brand: no fuzzy matching


def test_short_brand_only_matches_exact_or_lookalike():
    assert match_name("z0h0", "Zoho").verdict == FLAGGED
    assert match_name("zoho_support", "Zoho").verdict == FLAGGED
    assert match_name("zoho123", "Zoho").verdict == FLAGGED
    assert match_name("zohoo", "Zoho").verdict == SAFE  # typos on 4-letter brands are not matched (documented)
    assert match_name("Zoho Weather", "Zoho").verdict == SAFE
    assert match_name("zoom", "Zoho").verdict == SAFE


def test_brand_present_with_other_business_word_is_review_not_flagged():
    for c in ("Zenith Pay Bakery", "zenithpayhotels"):
        assert match_name(c, "Zenith Pay").verdict == REVIEW
    assert match_name("Zenith Bakery", "Zenith").verdict == REVIEW  # single-word brand: ambiguous, not flagged


def test_first_word_only_variants():
    assert match_name("Acme Support", BRAND).verdict == REVIEW  # first word + impersonation word
    assert match_name("Acme", BRAND).verdict == REVIEW
    assert match_name("Acme Plumbing", BRAND).verdict == SAFE  # different business
    assert match_name("Bank Support", BRAND).verdict == SAFE  # last word alone is generic
    assert match_name("River Bank", BRAND).verdict == SAFE
    assert match_name("Central Bank", BRAND).verdict == SAFE


def test_real_word_one_letter_away_downgraded():
    assert match_name("Acme Bark", BRAND).verdict == REVIEW
    assert match_name("Acme Banc", BRAND).verdict == FLAGGED  # "banc" is not an ordinary word


def test_partial_match_never_flagged_when_leftover_is_unknown_text():
    # "acmebak" is one edit from the brand but "ery"/"ing" cannot be explained -> full brand not present
    for c in ("acmebakery", "acmebaking", "Acme Bakers"):
        assert match_name(c, BRAND).verdict in (SAFE, REVIEW)


def test_configurable_benign_and_impersonation_words():
    assert NameMatcher(BRAND, benign_words={"gurukul"}).match("Acme Bank Gurukul").verdict == REVIEW
    assert NameMatcher(BRAND, impersonation_words={"loanwala"}).match("acmebank_loanwala").verdict == FLAGGED
    assert match_name("acmebank_loanwala", BRAND).verdict == REVIEW  # unknown glue: review only


def test_aliases_are_brand_tokens():
    m = NameMatcher(BRAND, aliases=["AcmeB"])
    assert m.match("acmeb_support").verdict == FLAGGED
    assert m.match("acmeb").verdict == BRAND_NAME
    assert match_name("acmeb_support", BRAND).verdict != FLAGGED


def test_multiword_brand_token_is_concatenation():
    r = match_name("hdfcbankhelp", "HDFC Bank")
    assert r.verdict == FLAGGED and r.matched_brand == "HDFC Bank"


def test_unrelated_names_safe():
    for c in ("Green Valley Farms", "Sunrise Yoga Studio", "Pixel Playground", "xyz", "12345", "Maple Leaf Cafe"):
        r = match_name(c, BRAND)
        assert r.verdict == SAFE and r.name_match_pct < 40


def test_empty_and_symbol_only():
    for c in ("", "   ", "@", "___", "!!!"):
        r = match_name(c, BRAND)
        assert r.verdict == SAFE and r.name_match_pct == 0


def test_result_is_explainable():
    r = match_name("Acme Bank Support", BRAND)
    assert r.notes and "support" in r.notes[0]
    assert r.matched_brand == BRAND and r.normalized == "acmebanksupport"
    assert 0 <= r.name_match_pct <= 100


def test_pct_ordering():
    exact = match_name("Acme Bank", BRAND).name_match_pct
    obf = match_name("acm3bank", BRAND).name_match_pct
    typo = match_name("acmebnak", BRAND).name_match_pct
    review = match_name("Acme Bake", BRAND).name_match_pct
    safe = match_name("Acme Hotels", BRAND).name_match_pct
    assert exact >= obf > typo > review > safe


def test_brand_with_digits_is_not_a_leet_technique():
    assert match_name("kotak811", "Kotak811").verdict == BRAND_NAME
    assert DIGIT_OR_SYMBOL_SWAP in match_name("k0tak811", "Kotak811").techniques


def test_invalid_brand():
    with pytest.raises(ValueError):
        NameMatcher("!!!")


def test_symbol_handling():
    assert match_name("@acmebank", BRAND).verdict == BRAND_NAME  # leading @ is stripped, not leet
    assert match_name("acm$bank", BRAND).verdict == FLAGGED  # $ inside a word acts as s -> one substitution
    assert match_name("acme_bank", BRAND).verdict == FLAGGED

import pytest

from app.core.engine.llm import (
    CachedScamIntentLLM,
    LLMIntentResult,
    LLMUnavailable,
    ScamIntentLLM,
    parse_llm_json,
    text_key,
)
from app.core.engine.text_signals import analyze_bio, description_overlap, scam_points

OFFICIAL = (
    "Acme Bank offers savings accounts, home loans and credit cards. Visit your nearest branch or use our "
    "mobile banking app to manage your money safely and securely anytime."
)


def ids(text, links=()):
    return {s.id for s in analyze_bio(text, links)}


def test_overlap_copy_vs_unrelated_vs_short():
    copy = description_overlap(OFFICIAL, OFFICIAL)
    assert copy.score == 100 and copy.shared_phrases
    partial = description_overlap("Acme Bank offers savings accounts, home loans and credit cards. Claim your free gift now!", OFFICIAL)
    assert 20 <= partial.score < 80
    assert any("savings accounts" in p for p in partial.shared_phrases)
    assert description_overlap("A fun puzzle game about cats and boxes with daily levels to solve", OFFICIAL).score == 0
    assert description_overlap("welcome to the app", OFFICIAL).score == 0
    assert description_overlap(None, OFFICIAL).score == 0
    assert description_overlap("x", None).score == 0


def test_overlap_ordering():
    reworded = description_overlap("Use our mobile banking app to manage your money safely and securely", OFFICIAL).score
    copy = description_overlap(OFFICIAL, OFFICIAL).score
    other = description_overlap("Great photo editor with filters and stickers for sharing pictures online", OFFICIAL).score
    assert copy > reworded > other


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Complete your KYC now or your account will be blocked", {"kyc_lure"}),
        ("Pay a small processing fee to release your loan", {"fee_request"}),
        ("registration fee Rs 500 only", {"fee_request"}),
        ("DM us for details", {"dm_contact"}),
        ("Contact on WhatsApp +91 98765 43210", {"messaging_contact"}),
        ("Join our Telegram t.me/acmehelp", {"messaging_contact"}),
        ("Get 10% cashback! Lucky draw winner announced", {"reward_lure"}),
        ("Limited time offer, hurry! Act now", {"urgency"}),
        ("Verify your account to continue", {"verify_account"}),
        ("Send money to our UPI id acme@ybl", {"payment_request"}),
        ("Share your OTP with our agent", {"payment_request"}),
        ("Double your money in 7 days, guaranteed returns", {"investment_promise"}),
        ("Customer care number: +91 1800 123 4567", {"helpline_number"}),
        ("Official account of Acme Bank", {"official_claim"}),
    ],
)
def test_scam_signals(text, expected):
    assert expected <= ids(text)


def test_link_shortener_in_bio_or_links():
    assert "link_shortener" in ids("Claim here bit.ly/abc123")
    assert "link_shortener" in ids("hello", ["https://tinyurl.com/x"])
    assert "link_shortener" not in ids("visit https://acmebank.com/help")


def test_clean_bio_has_no_signals_and_notes_are_plain_language():
    assert analyze_bio("Welcome to Acme Bank. Banking made simple.") == []
    assert analyze_bio(None) == []
    sigs = analyze_bio("Complete KYC. DM us. Limited time, pay processing fee.")
    assert len(sigs) >= 4 and all(s.note and s.matches for s in sigs)
    assert scam_points(sigs) == sum(s.points for s in sigs)


# ---- LLM -----------------------------------------------------------------------------------------


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def assess(self, text):
        self.calls += 1
        return LLMIntentResult(0.9, ["kyc_lure"], "looks like a KYC lure")


def test_fake_llm_satisfies_protocol():
    assert isinstance(FakeLLM(), ScamIntentLLM)


def test_cache_hits_by_text_hash_and_lru_eviction():
    inner = FakeLLM()
    c = CachedScamIntentLLM(inner, max_size=2)
    c.assess("a")
    c.assess("a")
    assert inner.calls == 1 and c.hits == 1 and c.misses == 1
    c.assess("b")
    c.assess("c")  # evicts "a" (least recently used)
    c.assess("a")
    assert inner.calls == 4
    assert text_key("a") == text_key("a") != text_key("b") and len(text_key("a")) == 64


def test_parse_llm_json_errors():
    with pytest.raises(LLMUnavailable):
        parse_llm_json("no json here")
    with pytest.raises(LLMUnavailable):
        parse_llm_json('{"categories": []}')
    assert parse_llm_json('{"scam_likelihood": -3}').scam_likelihood == 0.0

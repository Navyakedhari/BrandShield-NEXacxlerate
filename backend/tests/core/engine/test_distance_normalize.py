import pytest

from app.core.engine.distance import one_edit, osa_distance, osa_ops
from app.core.engine.normalize import classify_extras, fold_text, prepare, segment_words


@pytest.mark.parametrize(
    "a,b,d",
    [("", "", 0), ("abc", "", 3), ("", "abc", 3), ("kitten", "sitting", 3), ("ab", "ba", 1), ("acmebank", "acmebnak", 1), ("ca", "abc", 3)],
)
def test_osa_distance(a, b, d):
    assert osa_distance(a, b) == d


@pytest.mark.parametrize(
    "a,b,op",
    [("acmebank", "acmebnak", "trans"), ("acmebonk", "acmebank", "sub"), ("acmbank", "acmebank", "del"), ("acmeebank", "acmebank", "ins"), ("same", "same", None), ("abc", "xyz", None)],
)
def test_one_edit_and_ops_agree(a, b, op):
    assert one_edit(a, b) == op
    if op:
        assert osa_ops(a, b) == [op]


def test_osa_ops_two_edits():
    assert sorted(osa_ops("acmbnk", "acmebank")) == ["del", "del"]


@pytest.mark.parametrize(
    "raw,folded",
    [
        ("Acme Bank", "acmebank"),
        ("@acme_bank", "acmebank"),
        ("ACM3-B4NK", "acmebank"),
        ("acmеbank", "acmebank"),  # Cyrillic e
        ("ácmèbänk", "acmebank"),
        ("ＡＣＭＥ", "acme"),
        ("$ecure", "secure"),
        ("ácme", "acme"),
        ("5ecur1ty", "securlty"),
    ],
)
def test_fold_text(raw, folded):
    assert fold_text(raw) == folded


def test_fold_variants():
    assert fold_text("rnoney", digraph=True) == "money"
    assert fold_text("vvallet", digraph=True) == "wallet"
    assert fold_text("rnoney") == "rnoney"
    assert fold_text("orb1t", one_as="i") == "orbit"


def test_prepare_strips_leading_at_and_collapses_whitespace():
    text, items = prepare("  @Acme   Bank ")
    assert text == "Acme Bank"
    assert sum(1 for i in items if i.kind == "sep") == 1


def test_segment_words():
    assert segment_words("customercare") == ["customercare"]
    assert segment_words("helpteam") == ["help", "team"]
    assert segment_words("officialsupportindia") == ["official", "support", "india"]
    assert segment_words("zzzz") is None
    assert segment_words("supportzzz") is None
    assert segment_words("") == []


def test_classify_extras():
    assert classify_extras("123").kind == "digits"
    assert classify_extras("support 24").words == ["support", "24"]
    assert classify_extras("support24").kind == "dictionary"
    assert classify_extras("qqqq").kind == "other"
    assert classify_extras("") is None
    assert classify_extras("supp0rt").words == ["support"]

import json

from app.core.engine.name_match import TECHNIQUES, NameMatcher
from app.core.engine.synthetic import MIXED, NEGATIVE, generate_cases
from scripts.evaluate import BRANDS, evaluate_brand, main, merge


def test_generator_is_deterministic_and_seeded():
    a = generate_cases("Acme Bank", seed=7)
    b = generate_cases("Acme Bank", seed=7)
    c = generate_cases("Acme Bank", seed=8)
    assert a == b
    assert a != c


def test_generator_covers_every_technique_and_negative_kinds():
    cases = generate_cases("Acme Bank")
    kinds = {c.technique for c in cases}
    assert set(TECHNIQUES) | {MIXED, NEGATIVE} <= kinds
    groups = {c.group for c in cases if c.technique == NEGATIVE}
    assert groups == {"hard_negative", "unrelated", "fan_page", "authorized_reseller"}
    texts = {c.text for c in cases}
    assert {"Acme Bakery", "Acme Hotels", "Acme Weather", "Acme Motors", "Acme Textiles"} <= texts
    assert len(texts) == len(cases)  # no duplicates
    assert "Acme Bank" not in texts


def test_hard_negatives_never_flagged_for_any_brand():
    for brand in BRANDS:
        m = NameMatcher(brand)
        for c in generate_cases(brand, other_brands=[b for b in BRANDS if b != brand]):
            # authorized resellers carry the brand name: a name-only matcher flags them (documented limit; the allowlist clears them)
            if c.technique == NEGATIVE and c.group != "authorized_reseller":
                assert m.match(c.text).verdict != "flagged", (brand, c.text)


def test_evaluation_quality_floor():
    results = [evaluate_brand(b, seed=7, per_technique=25) for b in BRANDS]
    agg = merge(results)
    # every false positive is an authorized-reseller name (documented limit); everything else is never flagged
    assert all("Authori" in fp for r in results for fp in r["false_positives"])
    assert agg["overall"]["precision"] >= 0.97
    assert agg["overall"]["recall"] >= 0.93
    for t in (*TECHNIQUES, MIXED):
        assert agg[t]["precision"] >= 0.7  # per-technique rows share the reseller false positives
    # long and ordinary-length brands: every technique is caught
    for r in results:
        if r["brand"] != "Zoho":
            assert r["overall"]["recall"] >= 0.98, (r["brand"], r["misses"])
    # known, documented limit: 4-letter brands only match exact / look-alike / combo forms
    zoho = next(r for r in results if r["brand"] == "Zoho")
    assert zoho["techniques"]["added_word"]["recall"] == 1.0
    assert zoho["techniques"]["character_swap"]["recall"] < 0.5


def test_cli_table_and_json(capsys):
    assert main(["--brand", "Acme Bank", "--per-technique", "5"]) == 0
    out = capsys.readouterr().out
    assert "precision" not in out and "prec" in out and "OVERALL" in out and "COMBINED" in out
    assert main(["--json", "--brand", "Acme Bank", "--per-technique", "5"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["seed"] == 7 and data["brands"][0]["brand"] == "Acme Bank" and "overall" in data["combined"]

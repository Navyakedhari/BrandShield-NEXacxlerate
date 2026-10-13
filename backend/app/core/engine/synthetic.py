"""Deterministic synthetic test generator for the name matcher.

Mutates a brand name with every technique, and builds hard negatives (other businesses that share a word
with the brand) plus unrelated names. Used by tests and by `scripts/evaluate.py`. Seeded, so results are
reproducible. This is NOT ground truth for real-world attackers; it only checks the matcher is consistent
with the documented techniques.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .name_match import (
    ADDED_WORD,
    CHARACTER_SWAP,
    DIGIT_OR_SYMBOL_SWAP,
    HOMOGLYPH,
    MISSING_OR_EXTRA_LETTER,
    SPACING_OR_SEPARATOR,
    TRANSPOSED,
)

NEGATIVE = "negative"  # label for non-impersonation cases

_KEYBOARD = {
    "a": "sqwz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsdr", "f": "drtgvc", "g": "ftyhbv", "h": "gyujnb",
    "i": "ujko", "j": "huikmn", "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm", "o": "iklp", "p": "ol",
    "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy", "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc",
    "y": "tghu", "z": "asx",
}  # fmt: skip
_LEET = {"o": "0", "l": "1", "i": "1", "e": "3", "a": "4", "s": "5", "b": "8", "t": "7"}
_LEET_SYM = {"s": "$", "a": "@"}
_HOMOGLYPHS = {"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у", "i": "і", "s": "ѕ"}
_ADDED = [
    "support", "official", "help", "helpline", "care", "customer care", "rewards", "cashback", "service", "team",
    "india", "app", "login", "kyc", "refund", "verify", "helpdesk", "customer service", "claim", "giveaway",
]  # fmt: skip
BENIGN_SUFFIXES = [
    "Bakery", "Hotels", "Weather", "Motors", "Textiles", "Foods", "Restaurant", "Travels", "Jewellers", "Pharmacy",
    "School", "Furniture", "Dairy", "Cafe", "Studio", "Hardware", "Sports", "Salon",
]  # fmt: skip
UNRELATED = [
    "Green Valley Farms", "Blue Ocean Travels", "Sharma Electricals", "Maple Leaf Cafe", "Riverside Dental Care",
    "Golden Gate Realty", "Urban Threads", "Pixel Playground", "Silver Oak Tutors", "Sunrise Yoga Studio",
    "Falcon Logistics", "Coastal Seafoods", "Lotus Wellness", "Granite Works", "Vertex Cycles", "Harvest Moon Organics",
    "Willow Creek Books", "Northstar Cabs", "Iron Forge Gym", "Pearl Boutique",
]  # fmt: skip


@dataclass(frozen=True)
class Case:
    text: str
    technique: str  # one of the technique names, or NEGATIVE
    expect_flagged: bool
    group: str = ""  # sub-kind, e.g. "hard_negative" / "unrelated"


def _compact(brand: str) -> str:
    return "".join(brand.lower().split())


def _gen_character_swap(brand: str, rnd: random.Random, n: int) -> list[str]:
    c = _compact(brand)
    out = []
    for _ in range(n * 4):
        i = rnd.randrange(len(c))
        repl = rnd.choice(_KEYBOARD.get(c[i], "x"))
        out.append(c[:i] + repl + c[i + 1 :])
    return out


def _gen_digit_symbol(brand: str, rnd: random.Random, n: int) -> list[str]:
    c = _compact(brand)
    idxs = [i for i, ch in enumerate(c) if ch in _LEET]
    out = []
    for _ in range(n * 3):
        if not idxs:
            break
        chosen = rnd.sample(idxs, k=min(len(idxs), rnd.choice([1, 1, 2])))
        s = list(c)
        for i in chosen:
            s[i] = _LEET_SYM[c[i]] if c[i] in _LEET_SYM and rnd.random() < 0.3 else _LEET[c[i]]
        out.append("".join(s))
    return out


def _gen_added(brand: str, rnd: random.Random, n: int) -> list[str]:
    words = brand.split()
    out = []
    for _ in range(n * 2):
        w = rnd.choice(_ADDED)
        sep = rnd.choice(["", " ", "_", "-", "."])
        if sep == " ":
            base = " ".join(words)
        else:
            base = sep.join(words) if rnd.random() < 0.5 else "".join(words)
        form = rnd.choice(["suffix", "suffix", "prefix", "digits", "both"])
        if form == "suffix":
            t = base + sep + w
        elif form == "prefix":
            t = w + sep + base
        elif form == "digits":
            t = base + sep + str(rnd.choice([24, 247, 123, 2024, 1, 99]))
        else:
            t = f"{w}{sep}{base}{sep}{rnd.choice(['india', 'team', 'online'])}"
        out.append(t.title() if rnd.random() < 0.3 else t)
    return out


def _gen_spacing(brand: str, rnd: random.Random, n: int) -> list[str]:
    c = _compact(brand)
    words = brand.split()
    out = []
    for _ in range(n * 3):
        mode = rnd.choice(["sep_words", "split_letters", "insert"])
        sep = rnd.choice(["-", "_", ".", " ", "--", "_."])
        if mode == "sep_words" and len(words) > 1:
            out.append(sep.join(words))
        elif mode == "split_letters":
            k = rnd.randint(1, 3)
            pos = sorted(rnd.sample(range(1, len(c)), min(k, len(c) - 1)))
            s, prev = "", 0
            for p in pos:
                s += c[prev:p] + sep
                prev = p
            out.append(s + c[prev:])
        else:
            out.append(c + rnd.choice(["_", ".", "-", "__"]))
    return out


def _gen_transposed(brand: str, rnd: random.Random, n: int) -> list[str]:
    c = _compact(brand)
    out = []
    for i in range(len(c) - 1):
        if c[i] != c[i + 1]:
            out.append(c[:i] + c[i + 1] + c[i] + c[i + 2 :])
    rnd.shuffle(out)
    return out[: n * 2]


def _gen_missing_extra(brand: str, rnd: random.Random, n: int) -> list[str]:
    c = _compact(brand)
    out = []
    for _ in range(n * 3):
        i = rnd.randrange(len(c))
        if rnd.random() < 0.5:
            out.append(c[:i] + c[i + 1 :])  # missing
        elif rnd.random() < 0.5:
            out.append(c[: i + 1] + c[i] + c[i + 1 :])  # doubled letter
        else:
            out.append(c[:i] + rnd.choice("abcdefghijklmnopqrstuvwxyz") + c[i:])  # extra letter
    return out


def _gen_homoglyph(brand: str, rnd: random.Random, n: int) -> list[str]:
    c = _compact(brand)
    idxs = [i for i, ch in enumerate(c) if ch in _HOMOGLYPHS]
    out = []
    for _ in range(n * 2):
        mode = rnd.choice(["cyr", "cyr", "digraph", "fullwidth", "accent"])
        if mode == "cyr" and idxs:
            i = rnd.choice(idxs)
            out.append(c[:i] + _HOMOGLYPHS[c[i]] + c[i + 1 :])
        elif mode == "digraph" and "m" in c:
            i = c.index("m")
            out.append(c[:i] + "rn" + c[i + 1 :])
        elif mode == "digraph" and "w" in c:
            i = c.index("w")
            out.append(c[:i] + "vv" + c[i + 1 :])
        elif mode == "fullwidth":
            out.append("".join(chr(ord(ch) + 0xFEE0) if "a" <= ch <= "z" else ch for ch in c))
        else:
            accents = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "n": "ñ"}
            j = [i for i, ch in enumerate(c) if ch in accents]
            if j:
                k = rnd.choice(j)
                out.append(c[:k] + accents[c[k]] + c[k + 1 :])
    return out


def _gen_mixed(brand: str, rnd: random.Random, n: int) -> list[str]:
    """Two techniques at once (the realistic case): look-alike + added word, typo + separator, etc."""
    c = _compact(brand)
    out = []
    for _ in range(n * 3):
        kind = rnd.choice(["leet_word", "transposed_word", "homoglyph_word", "sep_leet", "missing_word"])
        w = rnd.choice(["support", "official", "help", "care", "rewards", "kyc", "india"])
        if kind == "leet_word":
            idxs = [i for i, ch in enumerate(c) if ch in _LEET]
            if not idxs:
                continue
            i = rnd.choice(idxs)
            out.append(c[:i] + _LEET[c[i]] + c[i + 1 :] + rnd.choice(["_", "", "."]) + w)
        elif kind == "transposed_word":
            i = rnd.randrange(len(c) - 1)
            if c[i] == c[i + 1]:
                continue
            out.append(c[:i] + c[i + 1] + c[i] + c[i + 2 :] + rnd.choice(["_", "-", ""]) + w)
        elif kind == "homoglyph_word":
            idxs = [i for i, ch in enumerate(c) if ch in _HOMOGLYPHS]
            if not idxs:
                continue
            i = rnd.choice(idxs)
            out.append(w + rnd.choice(["_", ""]) + c[:i] + _HOMOGLYPHS[c[i]] + c[i + 1 :])
        elif kind == "sep_leet":
            idxs = [i for i, ch in enumerate(c) if ch in _LEET]
            if not idxs or len(c) < 4:
                continue
            i = rnd.choice(idxs)
            s = c[:i] + _LEET[c[i]] + c[i + 1 :]
            p = rnd.randint(1, len(s) - 1)
            out.append(s[:p] + rnd.choice(["-", "_", "."]) + s[p:])
        else:
            i = rnd.randrange(len(c))
            out.append(c[:i] + c[i + 1 :] + rnd.choice(["_", ""]) + w)
    return out


MIXED = "mixed"

_GENERATORS = {
    CHARACTER_SWAP: _gen_character_swap,
    DIGIT_OR_SYMBOL_SWAP: _gen_digit_symbol,
    ADDED_WORD: _gen_added,
    SPACING_OR_SEPARATOR: _gen_spacing,
    TRANSPOSED: _gen_transposed,
    MISSING_OR_EXTRA_LETTER: _gen_missing_extra,
    HOMOGLYPH: _gen_homoglyph,
    MIXED: _gen_mixed,
}


def generate_cases(brand: str, seed: int = 7, per_technique: int = 25, other_brands: list[str] | None = None) -> list[Case]:
    """Positives for every technique, then hard negatives and unrelated names. Deterministic for a seed."""
    rnd = random.Random(f"{seed}:{brand}")
    cases: list[Case] = []
    seen: set[str] = {brand.lower(), _compact(brand)}
    for technique, gen in _GENERATORS.items():
        got = 0
        for t in gen(brand, rnd, per_technique):
            if got >= per_technique:
                break
            if t.lower() in seen or not t.strip():
                continue
            seen.add(t.lower())
            cases.append(Case(t, technique, True))
            got += 1

    words = brand.split()
    for suffix in BENIGN_SUFFIXES:
        if len(words) >= 2:
            # other businesses sharing the brand's first (or last) word
            for t in (f"{words[0]} {suffix}", f"{suffix} {words[-1]}", f"{words[0]}{suffix.lower()}"):
                if t.lower() not in seen:
                    seen.add(t.lower())
                    cases.append(Case(t, NEGATIVE, False, "hard_negative"))
        else:
            # single-word brand: brand + other-business word is genuinely ambiguous -> must not be *flagged*
            for t in (f"{brand} {suffix}", f"{brand}{suffix.lower()}"):
                if t.lower() not in seen:
                    seen.add(t.lower())
                    cases.append(Case(t, NEGATIVE, False, "hard_negative"))
    if len(words) >= 2:
        # other organisations sharing only the brand's generic last word ("River Bank", "Food Bank")
        for first in ("River", "Blood", "Food", "Central", "Seed", "Village"):
            t = f"{first} {words[-1]}"
            if t.lower() not in seen and _compact(t) != _compact(brand):
                seen.add(t.lower())
                cases.append(Case(t, NEGATIVE, False, "hard_negative"))
    # fan pages and authorized resellers: legitimate-looking names that carry the brand. Labelled NOT impersonation here
    # (a real analyst still reviews them); a "flagged" verdict on one counts as a false positive.
    for t in (f"{brand} Fans", f"{brand} Fan Club", f"{brand} Alumni Association", f"Friends of {brand}"):
        if t.lower() not in seen:
            seen.add(t.lower())
            cases.append(Case(t, NEGATIVE, False, "fan_page"))
    for t in (f"{brand} Authorized Reseller", f"{brand} Authorised Partner", f"Authorized {brand} Dealer"):
        if t.lower() not in seen:
            seen.add(t.lower())
            cases.append(Case(t, NEGATIVE, False, "authorized_reseller"))
    for name in [*UNRELATED, *(other_brands or [])]:
        if name.lower() not in seen and _compact(name) != _compact(brand):
            seen.add(name.lower())
            cases.append(Case(name, NEGATIVE, False, "unrelated"))
    return cases

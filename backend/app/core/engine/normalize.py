"""Name normalisation: look-alike folding with provenance, plus DP word segmentation."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .vocab import vocabulary

# Flags describing how a folded character differs from what was typed.
F_HOMOGLYPH = 1  # Unicode look-alike, fullwidth form or accented letter
F_LEET = 2  # digit/symbol standing in for a letter
F_DIGRAPH = 4  # two characters standing in for one (rn -> m, vv -> w)

LEET_DIGITS = {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b"}
LEET_SYMBOLS = {"$": "s", "@": "a", "!": "i", "|": "l"}

# Cyrillic / Greek / misc look-alikes -> ASCII (applied after NFKD + lowercase).
HOMOGLYPHS = {
    # Cyrillic
    "а": "a", "в": "b", "с": "c", "е": "e", "ё": "e", "о": "o", "р": "p", "х": "x", "у": "y", "і": "i",
    "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ԛ": "q", "ԝ": "w", "к": "k", "м": "m", "т": "t",
    "н": "h", "ѵ": "v", "ӏ": "l", "ɡ": "g", "ɑ": "a", "ı": "i", "ɩ": "i", "ℓ": "l",
    # Greek
    "α": "a", "β": "b", "γ": "y", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
    "υ": "u", "χ": "x", "ω": "w", "η": "n", "μ": "u",
    # Latin extras that NFKD does not decompose
    "ł": "l", "ø": "o", "đ": "d", "ħ": "h", "æ": "ae", "œ": "oe", "ß": "ss",
}  # fmt: skip


@dataclass(frozen=True)
class Item:
    """One typed character after unicode folding (before digit leet folding)."""

    ch: str  # folded lowercase char (digit still a digit)
    orig: str  # what was typed (lowercased)
    raw_idx: int
    kind: str  # "alnum" | "sep"
    flags: int


@dataclass(frozen=True)
class Seg:
    ch: str
    orig: str
    raw_start: int
    raw_end: int
    flags: int


def prepare(raw: str) -> tuple[str, list[Item]]:
    """Strip one leading '@', collapse whitespace, fold Unicode look-alikes. Returns (text, items)."""
    s = unicodedata.normalize("NFC", raw.strip())
    if s.startswith("@"):
        s = s[1:]
    s = re.sub(r"\s+", " ", s).strip()
    items: list[Item] = []
    for idx, c in enumerate(s):
        decomposed = unicodedata.normalize("NFKD", c)
        flags = 0
        out: list[str] = []
        for d in decomposed:
            if unicodedata.combining(d):
                flags |= F_HOMOGLYPH
                continue
            d = d.lower()
            mapped = HOMOGLYPHS.get(d)
            if mapped:
                out.append(mapped)
                flags |= F_HOMOGLYPH
            else:
                out.append(d)
        folded = "".join(out)
        if ord(c) > 127 and folded and all(ch.isascii() for ch in folded):
            flags |= F_HOMOGLYPH
        orig = c.lower()
        if not folded:
            continue
        if folded.isalnum():
            for ch in folded:
                items.append(Item(ch, orig, idx, "alnum", flags))
        elif folded in LEET_SYMBOLS:
            prev_ok = idx > 0 and s[idx - 1].isalnum()
            next_ok = idx + 1 < len(s) and s[idx + 1].isalnum()
            if (prev_ok and next_ok) or (folded != "@" and (prev_ok or next_ok)):
                items.append(Item(LEET_SYMBOLS[folded], orig, idx, "alnum", flags | F_LEET))
            else:
                items.append(Item(" ", orig, idx, "sep", 0))
        else:
            items.append(Item(" ", orig, idx, "sep", 0))
    return s, items


def segments(items: list[Item], one_as: str = "l", digraph: bool = False) -> list[Seg]:
    """Fold digits (leet) and optionally visual digraphs into a collapsed character sequence."""
    segs: list[Seg] = []
    for it in items:
        if it.kind != "alnum":
            continue
        ch, flags = it.ch, it.flags
        if ch in LEET_DIGITS:
            ch = "i" if (ch == "1" and one_as == "i") else LEET_DIGITS[ch]
            flags |= F_LEET
        segs.append(Seg(ch, it.orig, it.raw_idx, it.raw_idx + 1, flags))
    if not digraph:
        return segs
    merged: list[Seg] = []
    k = 0
    while k < len(segs):
        a = segs[k]
        b = segs[k + 1] if k + 1 < len(segs) else None
        if b is not None and b.raw_start == a.raw_end:
            pair = a.ch + b.ch
            if pair in ("rn", "vv"):
                merged.append(Seg("m" if pair == "rn" else "w", a.orig + b.orig, a.raw_start, b.raw_end, a.flags | b.flags | F_DIGRAPH))
                k += 2
                continue
        merged.append(a)
        k += 1
    return merged


def collapse(segs: list[Seg]) -> str:
    return "".join(s.ch for s in segs)


def fold_text(text: str, one_as: str = "l", digraph: bool = False) -> str:
    """Convenience: fully normalised collapsed form of `text` (lowercase, no separators)."""
    return collapse(segments(prepare(text)[1], one_as, digraph))


# ---- word segmentation (DP over a small vocabulary) ---------------------------------------------

_VOCAB = vocabulary()
_MAX_WORD = max(len(w) for w in _VOCAB)


def segment_words(text: str, extra: dict[str, int] | None = None) -> list[str] | None:
    """Split `text` (letters only) into known words by dynamic programming.

    Minimises the number of words, preferring impersonation words on ties. Returns None if some
    part cannot be explained by the vocabulary (unknown glue).
    """
    if not text:
        return []
    vocab = _VOCAB if not extra else {**_VOCAB, **extra}
    n = len(text)
    inf = 10**9
    best = [inf] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0
    for i in range(1, n + 1):
        for k in range(1, min(i, _MAX_WORD) + 1):
            w = text[i - k : i]
            rank = vocab.get(w)
            if rank is None or best[i - k] >= inf:
                continue
            cost = best[i - k] + 10 + rank
            if cost < best[i]:
                best[i] = cost
                back[i] = k
    if best[n] >= inf:
        return None
    out: list[str] = []
    i = n
    while i > 0:
        out.append(text[i - back[i] : i])
        i -= back[i]
    out.reverse()
    return out


@dataclass(frozen=True)
class Extras:
    words: list[str]
    kind: str  # "digits" | "dictionary" | "other"


def classify_extras(text: str, extra_vocab: dict[str, int] | None = None) -> Extras | None:
    """Classify the leftover text around a brand token. None when there is nothing left."""
    parts = [p for p in text.split(" ") if p]
    if not parts:
        return None
    words: list[str] = []
    all_known = True
    for part in parts:
        if re.fullmatch(r"\d+x\d+", part):  # "24x7"
            words.append(part)
            continue
        pieces = re.findall(r"\d+|[^\d]+", part)
        got = _segment_pieces(pieces, extra_vocab)
        if got is None:
            # try the leet-folded reading of the whole part ("supp0rt")
            folded = "".join(LEET_DIGITS.get(c, c) for c in part)
            alt = segment_words(folded, extra_vocab) if folded.isalpha() else None
            if alt is not None:
                got = alt
        if got is None:
            all_known = False
            words.append(part)
        else:
            words.extend(got)
    if all_known and all(re.fullmatch(r"\d+(x\d+)?", w) for w in words):
        return Extras(words, "digits")
    if all_known:
        return Extras(words, "dictionary")
    return Extras(words, "other")


def _segment_pieces(pieces: list[str], extra_vocab: dict[str, int] | None) -> list[str] | None:
    out: list[str] = []
    for p in pieces:
        if p.isdigit():
            out.append(p)
            continue
        seg = segment_words(p, extra_vocab)
        if seg is None:
            return None
        out.extend(seg)
    return out

"""Name look-alike matcher.

Decision flow for one candidate name against one brand token (tried in this order):

1. Whole name equals the brand after folding.   -> exact (`brand_name`) or obfuscated (`flagged`).
2. Brand token present as a whole + extra text.  -> combo squat. Flagged only when the extra words
   are digits or impersonation words (support, official, kyc...). Other extras -> `review`.
3. Whole name within ONE edit of the brand token (swap, missing, extra, substitution).  -> typo.
4. A window of the name within one edit of the brand token AND every leftover piece is a known word
   (typo + combo).  Unknown leftovers ("ery" in "acmebakery") mean the full brand is NOT present, so
   nothing is flagged.  This is the principled reason "Acme Bakery" is never a hit for "Acme Bank".
5. Whole name within TWO edits (long brands only).  -> fuzzy, always `review`.
6. Only the brand's first word is present (multi-word brands).  -> `safe`, or `review` if the rest is
   an impersonation word ("Acme Support").
7. Otherwise `safe`.

Folding (lowercase, '@' strip, 0/o 1/l 3/e..., Unicode homoglyphs, accents, fullwidth, rn->m, vv->w,
separators removed) is applied to candidate and brand identically; every folded character remembers
what was typed so the matcher can report *which techniques* were used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .distance import one_edit, osa_distance, osa_ops
from .normalize import (
    F_DIGRAPH,
    F_HOMOGLYPH,
    F_LEET,
    Extras,
    Item,
    Seg,
    classify_extras,
    collapse,
    fold_text,
    prepare,
    segments,
)
from .vocab import BENIGN_TRAILING_WORDS, IMPERSONATION_WORDS, STRONG_SCAM_WORDS, vocabulary

# verdicts
FLAGGED = "flagged"
BRAND_NAME = "brand_name"
REVIEW = "review"
SAFE = "safe"

# techniques
CHARACTER_SWAP = "character_swap"
DIGIT_OR_SYMBOL_SWAP = "digit_or_symbol_swap"
ADDED_WORD = "added_word"
SPACING_OR_SEPARATOR = "spacing_or_separator"
TRANSPOSED = "transposed"
MISSING_OR_EXTRA_LETTER = "missing_or_extra_letter"
HOMOGLYPH = "homoglyph"

TECHNIQUES = (
    CHARACTER_SWAP,
    DIGIT_OR_SYMBOL_SWAP,
    ADDED_WORD,
    SPACING_OR_SEPARATOR,
    TRANSPOSED,
    MISSING_OR_EXTRA_LETTER,
    HOMOGLYPH,
)

TECHNIQUE_LABELS = {
    CHARACTER_SWAP: "character swap",
    DIGIT_OR_SYMBOL_SWAP: "digit or symbol swap",
    ADDED_WORD: "added word",
    SPACING_OR_SEPARATOR: "spacing or separator",
    TRANSPOSED: "transposed letters",
    MISSING_OR_EXTRA_LETTER: "missing or extra letter",
    HOMOGLYPH: "homoglyph (look-alike character)",
}

# squat categories
TYPO, COMBO, FUZZY, EXACT, NONE = "typo", "combo", "fuzzy", "exact", "none"

MIN_LEN_ONE_EDIT = 5  # shorter brand tokens are only matched exactly (too many accidental hits)
MIN_LEN_TWO_EDITS = 8


@dataclass
class NameMatchResult:
    verdict: str
    name_match_pct: int
    techniques: list[str] = field(default_factory=list)
    squat_category: str = NONE
    extra_word_kind: str | None = None  # digits | dictionary | other
    added_words: list[str] = field(default_factory=list)
    impersonation_words: list[str] = field(default_factory=list)
    edit_distance: int | None = None
    matched_brand: str | None = None
    normalized: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.verdict == FLAGGED


@dataclass(frozen=True)
class _Token:
    name: str
    words: tuple[str, ...]
    handle: str  # lowercase, no spaces (raw, unfolded)


def _split_words(name: str) -> list[str]:
    parts = re.split(r"[\s\-_.]+", name.strip().lstrip("@"))
    return [f for f in (fold_text(w) for w in parts) if f]


class NameMatcher:
    def __init__(
        self,
        brand_name: str,
        aliases: tuple[str, ...] | list[str] = (),
        *,
        benign_words: frozenset[str] | set[str] | None = None,
        impersonation_words: frozenset[str] | set[str] | None = None,
    ) -> None:
        self.brand_name = brand_name
        extra_benign = frozenset(w.lower() for w in (benign_words or ()))
        extra_imp = frozenset(w.lower() for w in (impersonation_words or ()))
        self.benign = frozenset(BENIGN_TRAILING_WORDS) | extra_benign
        self.imp = frozenset(IMPERSONATION_WORDS) | extra_imp
        self.strong = frozenset(STRONG_SCAM_WORDS) | extra_imp
        self._extra_vocab = {w: 2 for w in extra_benign}
        self._extra_vocab.update({w: 1 for w in extra_imp})
        self._all_words = frozenset(vocabulary()) | self.benign | self.imp
        self.tokens: list[_Token] = []
        seen: set[str] = set()
        for nm in [brand_name, *aliases]:
            words = tuple(_split_words(nm))
            handle = "".join(nm.lower().split()).lstrip("@")
            if not words or handle in seen:
                continue
            seen.add(handle)
            self.tokens.append(_Token(nm, words, handle))
        if not self.tokens:
            raise ValueError("brand name must contain letters or digits")
        self._fold_cache: dict[tuple[str, bool], list[tuple[str, list[str]]]] = {}

    # -- public ---------------------------------------------------------------------------------
    def match(self, candidate: str) -> NameMatchResult:
        text, items = prepare(candidate or "")
        if not any(i.kind == "alnum" for i in items):
            return NameMatchResult(SAFE, 0, notes=["Empty name."])
        base = collapse(segments(items))
        variants = [("l", False)]
        if any(i.ch == "1" for i in items):
            variants.append(("i", False))
        if "rn" in base or "vv" in base:
            variants.append(("l", True))
            if "1" in text:
                variants.append(("i", True))
        best: NameMatchResult | None = None
        best_key = (-1, 0)
        for one_as, digraph in variants:
            segs = segments(items, one_as, digraph)
            folded = collapse(segs)
            if not folded:
                continue
            for tok, (brand, bwords) in zip(self.tokens, self._folded_tokens(one_as, digraph), strict=True):
                if not brand:
                    continue
                res = self._match_one(text, items, segs, folded, tok, brand, bwords)
                key = (res.name_match_pct, -len(res.techniques))
                if key > best_key:
                    best, best_key = res, key
        return best or NameMatchResult(SAFE, 0, notes=["Empty name."])

    # -- internals ------------------------------------------------------------------------------
    def _folded_tokens(self, one_as: str, digraph: bool) -> list[tuple[str, list[str]]]:
        key = (one_as, digraph)
        if key not in self._fold_cache:
            self._fold_cache[key] = [
                (fold_text(t.name, one_as, digraph), [fold_text(w, one_as, digraph) for w in t.words]) for t in self.tokens
            ]
        return self._fold_cache[key]

    def _extras(self, items: list[Item], lo: int, hi: int) -> Extras | None:
        """Classify typed text outside raw index range [lo, hi)."""
        pre = "".join(i.ch if i.kind == "alnum" else " " for i in items if i.raw_idx < lo)
        post = "".join(i.ch if i.kind == "alnum" else " " for i in items if i.raw_idx >= hi)
        text = f"{pre} {post}".strip()
        if not text:
            return None
        return classify_extras(text, self._extra_vocab)

    def _techniques(
        self,
        segs: list[Seg],
        i: int,
        j: int,
        items: list[Item],
        tok: _Token,
        bwords: list[str],
        ops: list[str],
        extras: Extras | None,
    ) -> list[str]:
        out: set[str] = set()
        span = segs[i:j]
        brand_chars = set(tok.handle)
        for s in span:
            if s.flags & (F_HOMOGLYPH | F_DIGRAPH):
                out.add(HOMOGLYPH)
            if s.flags & F_LEET and any(c not in brand_chars for c in s.orig):
                out.add(DIGIT_OR_SYMBOL_SWAP)
        lo, hi = span[0].raw_start, span[-1].raw_end
        seps = [it for it in items if it.kind == "sep" and lo < it.raw_idx < hi]
        if seps:
            bounds: list[int] = []
            acc = 0
            for w in bwords[:-1]:
                acc += len(w)
                bounds.append(acc)
            groups: list[list[Item]] = []
            for it in seps:
                if groups and it.raw_idx == groups[-1][-1].raw_idx + 1:
                    groups[-1].append(it)
                else:
                    groups.append([it])
            tol = 1 if ops else 0  # with an edit the boundary offset may shift by one letter
            free = list(bounds)  # each word boundary may be a plain space only once
            for g in groups:
                k = sum(1 for s in span if s.raw_end <= g[0].raw_idx)
                hit = next((b for b in free if abs(k - b) <= tol), None) if len(g) == 1 and g[0].orig == " " else None
                if hit is None:
                    out.add(SPACING_OR_SEPARATOR)
                else:
                    free.remove(hit)
        elif extras is None and any(it.kind == "sep" for it in items):
            out.add(SPACING_OR_SEPARATOR)  # "acmebank_" / ".acmebank"
        for op in ops:
            if op == "sub":
                out.add(CHARACTER_SWAP)
            elif op == "trans":
                out.add(TRANSPOSED)
            else:
                out.add(MISSING_OR_EXTRA_LETTER)
        if extras is not None:
            out.add(ADDED_WORD)
        return [t for t in TECHNIQUES if t in out]

    def _build(self, verdict, pct, techniques, category, tok, folded, dist, extras, notes) -> NameMatchResult:
        words = list(extras.words) if extras else []
        return NameMatchResult(
            verdict=verdict,
            name_match_pct=max(0, min(100, int(pct))),
            techniques=techniques,
            squat_category=category,
            extra_word_kind=extras.kind if extras else None,
            added_words=words,
            impersonation_words=[w for w in words if w in self.imp],
            edit_distance=dist,
            matched_brand=tok.name,
            normalized=folded,
            notes=notes,
        )

    def _extras_outcome(self, extras: Extras, short: bool) -> tuple[str, int, str]:
        words = extras.words
        strong = [w for w in words if w in self.strong]
        imp = [w for w in words if w in self.imp]
        if short and not imp and extras.kind != "digits":
            return SAFE, 30, "The brand is a very short name, so other added words are not treated as impersonation."
        if extras.kind == "digits":
            return FLAGGED, 84, "The brand name is combined with numbers only (a common look-alike trick)."
        if strong:
            return FLAGGED, 92, f"The brand name is combined with scam-flavoured words ({', '.join(strong)})."
        if imp:
            return FLAGGED, 86, f"The brand name is combined with words often used to pose as the company ({', '.join(imp)})."
        if extras.kind == "dictionary" and all(w in self.benign for w in words):
            return REVIEW, 50, f"The full brand name is present with an other-business word ({' '.join(words)}); could be unrelated."
        if extras.kind == "dictionary":
            return REVIEW, 62, f"The full brand name is present with extra words ({' '.join(words)}); needs a human look."
        return REVIEW, 58, f"The full brand name is present inside a longer name ({' '.join(words)}); needs a human look."

    def _match_one(self, text, items, segs, S, tok, B, bwords) -> NameMatchResult:  # noqa: C901
        n, m = len(S), len(B)
        raw_dist = osa_distance(text.lower(), tok.handle)
        label = f'"{tok.name}"'

        def typo_pct(d: int) -> int:
            return max(70, min(93, round(100 * (1 - d / m))))

        # 1. whole name equals brand
        if S == B:
            techs = self._techniques(segs, 0, len(segs), items, tok, bwords, [], None)
            if not techs:
                note = f"Uses the exact brand name {label}. It may be the brand itself or someone using its name; check the allowlist."
                return self._build(BRAND_NAME, 100, [], EXACT, tok, S, 0, None, [note])
            names = ", ".join(TECHNIQUE_LABELS[t] for t in techs)
            note = f"Spelled to look like {label} ({names}) but is not the exact name."
            return self._build(FLAGGED, 96, techs, TYPO if raw_dist == 1 else FUZZY, tok, S, 0, None, [note])

        # 2. brand token present as a whole + extras
        pos = S.find(B)
        if pos >= 0:
            i, j = pos, pos + m
            extras = self._extras(items, segs[i].raw_start, segs[j - 1].raw_end)
            if extras is not None:
                if extras.kind != "digits" and n - m == 1 and m >= MIN_LEN_ONE_EDIT:
                    techs = self._techniques(segs, 0, len(segs), items, tok, bwords, ["ins"], None)
                    return self._build(FLAGGED, typo_pct(1), techs, TYPO, tok, S, 1, None, [f"One extra letter compared with {label}."])
                techs = self._techniques(segs, i, j, items, tok, bwords, [], extras)
                verdict, pct, note = self._extras_outcome(extras, m <= 4)
                return self._build(verdict, pct, techs, COMBO, tok, S, 0, extras, [note])

        # 3. whole name within one edit
        if m >= MIN_LEN_ONE_EDIT:
            op = one_edit(S, B)
            if op:
                techs = self._techniques(segs, 0, len(segs), items, tok, bwords, [op], None)
                if len(bwords) >= 2 and len(bwords[0]) >= 3 and S.startswith(bwords[0]):
                    rest = S[len(bwords[0]) :]
                    if rest in self._all_words and rest not in self.imp:
                        note = f'Differs from {label} by one letter, but "{rest}" is an ordinary word, so it may be a different business.'
                        return self._build(REVIEW, 55, techs, NONE, tok, S, 1, None, [note])
                names = ", ".join(TECHNIQUE_LABELS[t] for t in techs)
                return self._build(FLAGGED, typo_pct(1), techs, TYPO, tok, S, 1, None, [f"One edit away from {label} ({names})."])

            # 4. window within one edit + fully explained leftovers
            found = None
            for length in (m, m - 1, m + 1):
                for i in range(0, n - length + 1):
                    op = one_edit(S[i : i + length], B)
                    if not op:
                        continue
                    j = i + length
                    extras = self._extras(items, segs[i].raw_start, segs[j - 1].raw_end)
                    if extras is None or extras.kind == "other":
                        continue
                    verdict, pct, note = self._extras_outcome(extras, False)
                    pct = min(typo_pct(1), pct) - 2
                    if verdict == REVIEW:
                        pct = min(pct, 62)
                    rank = (verdict == FLAGGED, pct, -i)
                    if found is None or rank > found[0]:
                        found = (rank, i, j, op, extras, verdict, pct, note)
            if found:
                _, i, j, op, extras, verdict, pct, note = found
                techs = self._techniques(segs, i, j, items, tok, bwords, [op], extras)
                return self._build(verdict, pct, techs, COMBO, tok, S, 1, extras, [f"One edit away from {label} plus added words. {note}"])

        # 5. fuzzy: two edits, long brands only
        if m >= MIN_LEN_TWO_EDITS and abs(n - m) <= 2 and osa_distance(S, B) == 2:
            techs = self._techniques(segs, 0, len(segs), items, tok, bwords, osa_ops(S, B), None)
            pct = min(79, round(100 * (1 - 2 / m)))
            return self._build(REVIEW, pct, techs, FUZZY, tok, S, 2, None, [f"Two edits away from {label}; similar but not conclusive, needs a human look."])

        # 6. only the first word of a multi-word brand
        if len(bwords) >= 2 and len(bwords[0]) >= 3:
            first = bwords[0]
            if S == first:
                return self._build(REVIEW, 40, [], NONE, tok, S, None, None, [f"Only the first word of {label} is used; the rest of the brand name is missing."])
            at_start = S.startswith(first)
            at_end = S.endswith(first)
            if at_start or at_end:
                lo_i, hi_i = (0, len(first)) if at_start else (n - len(first), n)
                extras = self._extras(items, segs[lo_i].raw_start, segs[hi_i - 1].raw_end)
                if extras is not None:
                    imp = [w for w in extras.words if w in self.imp]
                    if imp and extras.kind != "other":
                        note = f"Uses the first word of {label} with an impersonation word ({', '.join(imp)}); needs a human look."
                        return self._build(REVIEW, 55, [], NONE, tok, S, None, extras, [note])
                    if at_start:
                        what = "a different business" if all(w in self.benign for w in extras.words) else "other words"
                        note = f"Shares only the first word of {label}; the rest ({' '.join(extras.words)}) points to {what}, not the brand."
                        return self._build(SAFE, 22, [], NONE, tok, S, None, extras, [note])

        sim = max(0.0, 1 - osa_distance(S, B) / max(n, m))
        return self._build(SAFE, min(35, round(35 * sim)), [], NONE, tok, S, None, None, ["No meaningful resemblance to the brand name."])


def match_name(candidate: str, brand_name: str, aliases: tuple[str, ...] | list[str] = (), **kwargs) -> NameMatchResult:
    return NameMatcher(brand_name, aliases, **kwargs).match(candidate)

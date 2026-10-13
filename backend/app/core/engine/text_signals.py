"""Text analysis: description overlap (shared phrases) and heuristic scam-intent signals in bios.

Heuristics only: each signal has plain-language notes so a reviewer can see why it fired. They are weak
evidence on their own; the scorer caps the bio category.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


# ---- description overlap -------------------------------------------------------------------------


@dataclass(frozen=True)
class OverlapResult:
    score: int  # 0-100
    shared_phrases: list[str] = field(default_factory=list)
    candidate_in_official: float = 0.0  # share of the candidate's n-grams found in the official text
    official_in_candidate: float = 0.0
    note: str = ""


def _ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def description_overlap(candidate: str | None, official: str | None, n: int = 3, min_ngrams: int = 3) -> OverlapResult:
    """Share of word n-grams (default trigrams) that appear in both texts.

    score = 100 * mean(containment of candidate in official, containment of official in candidate).
    A short generic candidate cannot score high (needs at least `min_ngrams` n-grams) so "welcome to the
    app" is not a copy. Consecutive shared n-grams are merged into phrases for display.
    """
    ct, ot = tokenize(candidate or ""), tokenize(official or "")
    cg, og = _ngrams(ct, n), _ngrams(ot, n)
    if len(cg) < min_ngrams or len(og) < min_ngrams:
        return OverlapResult(0, note="Not enough text to compare.")
    cset, oset = set(cg), set(og)
    shared = cset & oset
    c_in_o = len(shared) / len(cset)
    o_in_c = len(shared) / len(oset)
    score = round(100 * (c_in_o + o_in_c) / 2)

    phrases: list[str] = []
    run: list[str] = []
    for i, g in enumerate(cg):
        if g in shared:
            run = list(g) if not run else [*run, g[-1]]
        elif run:
            phrases.append(" ".join(run))
            run = []
        if i == len(cg) - 1 and run:
            phrases.append(" ".join(run))
    phrases = sorted(dict.fromkeys(phrases), key=len, reverse=True)[:5]
    if score == 0:
        note = "No shared phrases with the official description."
    else:
        note = f"{round(c_in_o * 100)}% of the candidate's wording appears in the official description."
    return OverlapResult(score, phrases, round(c_in_o, 3), round(o_in_c, 3), note)


# ---- scam intent signals -------------------------------------------------------------------------


@dataclass(frozen=True)
class ScamSignal:
    id: str
    points: int
    note: str
    matches: list[str] = field(default_factory=list)


_SHORTENERS = r"(?:bit\.ly|tinyurl\.com|t\.co|cutt\.ly|goo\.gl|is\.gd|rb\.gy|ow\.ly|shorturl\.at|tiny\.cc|rebrand\.ly|bl\.ink|s\.id|t\.ly)"

_RULES: list[tuple[str, int, str, str]] = [
    ("kyc_lure", 9, "Mentions KYC or an account freeze, a common lure to get people to hand over details.",
     r"\bkyc\b|know your customer|(?:update|link|verify)\s+(?:your\s+)?(?:pan|aadhaar|aadhar)|account\s+(?:will\s+be\s+|is\s+)?(?:blocked|suspended|frozen|deactivated)"),
    ("fee_request", 12, "Asks for a processing, registration or similar fee up front.",
     r"(?:processing|registration|activation|verification|clearance|joining|handling|insurance)\s+(?:fee|charges?)|security\s+deposit|refundable\s+(?:fee|deposit)|token\s+amount|advance\s+payment"),
    ("dm_contact", 5, "Pushes people to message privately (DM / inbox) instead of official channels.",
     r"\b(?:dm|pm)\s+(?:us|me)\b|\binbox\s+(?:us|me)\b|message\s+us\s+(?:now|on|for)|contact\s+us\s+(?:on|via)\s+(?:whatsapp|telegram|dm)"),
    ("messaging_contact", 6, "Gives a WhatsApp or Telegram contact; real brands rarely run support from those.",
     r"whats\s?app|\bwa\.me\b|\bt\.me/|telegram|\bsignal\s+(?:me|us|number)"),
    ("reward_lure", 8, "Promises cashback, rewards, prizes or giveaways.",
     r"cash\s?back|\bgive\s?aways?\b|lucky\s+(?:draw|winner)|\bwinners?\b|free\s+gift|\bprize\b|lottery|reward\s+points|claim\s+(?:your\s+)?(?:reward|prize|gift)"),
    ("urgency", 5, "Creates urgency (limited time, act now) to rush people.",
     r"limited\s+time|act\s+now|hurry|last\s+chance|today\s+only|expires?\s+(?:today|soon|in)|within\s+\d+\s+(?:hours?|hrs?|minutes?)|\burgent(?:ly)?\b"),
    ("verify_account", 8, "Asks people to verify or re-activate their account.",
     r"verify\s+(?:your\s+)?(?:account|identity|details)|account\s+verification|confirm\s+your\s+(?:identity|details|account)|re-?activate\s+(?:your\s+)?account|unlock\s+(?:your\s+)?account"),
    ("payment_request", 10, "Mentions UPI, wallets, gift cards or crypto transfers, or asks for OTP/PIN.",
     r"\b[a-z0-9._-]{2,}@(?:ok(?:axis|sbi|hdfcbank|icici)|ybl|paytm|ibl|axl|upi|apl)\b|\bupi\b|wallet\s+address|send\s+(?:money|crypto|funds)|gift\s+cards?|\busdt\b|\b(?:share|send|tell)\s+(?:your\s+)?(?:otp|pin|cvv|password)\b"),
    ("investment_promise", 10, "Promises guaranteed or doubled returns.",
     r"double\s+your\s+(?:money|investment)|guaranteed\s+(?:returns?|profit)|\b\d{2,3}%\s+returns?\b|trading\s+signals?|crypto\s+investment"),
    ("helpline_number", 5, "Lists a 'helpline' or 'customer care' phone number in the bio; scammers publish fake ones.",
     r"(?:helpline|customer\s+care|toll\s*free|support)\s*(?:no\.?|number|#|:)?\s*[:\-]?\s*\+?\d[\d\s\-]{7,}"),
    ("official_claim", 3, "Claims to be the official or authorised account.",
     r"official\s+(?:account|page|support|helpline|handle)|authori[sz]ed\s+(?:partner|dealer|agent|support)"),
]
_COMPILED = [(i, p, note, re.compile(rx, re.I)) for i, p, note, rx in _RULES]
_SHORTENER_RX = re.compile(rf"(?:https?://)?(?:www\.)?{_SHORTENERS}\b", re.I)


def analyze_bio(text: str | None, links: list[str] | tuple[str, ...] = ()) -> list[ScamSignal]:
    """Return matched scam-intent signals for a bio (plus any bio links)."""
    blob = " ".join([text or "", *links])
    out: list[ScamSignal] = []
    for sid, points, note, rx in _COMPILED:
        hits = [m.group(0).strip() for m in rx.finditer(blob)]
        if hits:
            out.append(ScamSignal(sid, points, note, list(dict.fromkeys(hits))[:3]))
    short = [m.group(0) for m in _SHORTENER_RX.finditer(blob)]
    if short:
        out.append(ScamSignal("link_shortener", 5, "Uses a link shortener, which hides where the link really goes.", list(dict.fromkeys(short))[:3]))
    return out


def scam_points(signals: list[ScamSignal]) -> int:
    return sum(s.points for s in signals)

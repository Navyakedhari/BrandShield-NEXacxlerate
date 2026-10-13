"""Sample collector: deterministic, clearly synthetic candidates for ANY brand profile.

Used when the data mode resolves to `sample` (never in live mode). EVERY candidate has source="sample".
It produces, seeded by brand id and name:
  * echoes of the brand's own official assets (to prove the allowlist excludes them),
  * mutated look-alikes in three tiers (strong / medium / weak). The tier sets bio, account age and which generated
    logo variant they carry, so a sample scan spans Low, Medium and High through the unchanged scorer,
  * hard negatives: other businesses that share one word with the brand.
When the brand has an uploaded logo, candidates carry GENERATED image bytes (sample_images.py: variants of that logo, or an
unrelated image). Only this collector produces them and the pipeline honours them only for source="sample".
Names are derived only from the user's own brand profile plus generic business words. It never invents
real people or real brands.
"""

from __future__ import annotations

import random
import re
from datetime import timedelta

from app.core.collectors.base import WEB_PLATFORM, BrandQuery, Candidate, CollectContext, Collector, candidate_from_web_url
from app.core.collectors.sample_images import make_variant
from app.core.engine.name_match import FLAGGED, NameMatcher
from app.core.engine.synthetic import BENIGN_SUFFIXES, generate_cases
from app.core.engine.urls import parse_profile_url, parse_store_url
from app.core.engine.vocab import STRONG_SCAM_WORDS

SOURCE = "sample"
_LURES = [
    "Official customer support. DM us to verify your KYC and claim your cashback today.",
    "24x7 helpline. Account blocked? Message us now to unblock. Rewards and refund claims here.",
    "Congratulations! You have won a reward. Click the link in bio to claim before it expires.",
    "Official support desk. Share your account details in DM for instant refund.",
    "Update your KYC within 24 hours to avoid suspension. Contact our helpline.",
]
_NEUTRAL_BIOS = [
    "Family run business serving the neighbourhood since 2009.",
    "Fresh every day. Open 9 to 6, closed Sundays.",
    "Local shop and online orders. Say hello!",
]
_SOCIAL = ("instagram", "facebook", "x", "telegram", "linkedin")
_STORES = ("google_play", "app_store", "third_party_apk")
_HANDLE_MAX = {"instagram": 30, "x": 15, "telegram": 32, "facebook": 50, "linkedin": 100}


def _slug(text: str, platform: str) -> str:
    s = re.sub(r"[^a-z0-9._]", "", text.lower().replace(" ", "").replace("-", "_"))[: _HANDLE_MAX.get(platform, 30)]
    if platform == "x":
        s = s.replace(".", "")
    if platform == "telegram":
        s = s.replace(".", "_").lstrip("0123456789_")
    return s


def _profile_url(platform: str, handle: str) -> str:
    return {
        "instagram": f"https://www.instagram.com/{handle}/",
        "facebook": f"https://www.facebook.com/{handle}",
        "x": f"https://x.com/{handle}",
        "telegram": f"https://t.me/{handle}",
        "linkedin": f"https://www.linkedin.com/company/{handle}",
    }[platform]


def _valid_profile(platform: str, url: str) -> bool:
    p = parse_profile_url(url)
    return p.ok and p.platform == platform


class SampleCollector(Collector):
    name = "sample"
    kind = "mixed"
    platforms = ("instagram", "facebook", "x", "telegram", "linkedin", "google_play", "app_store", "third_party_apk", WEB_PLATFORM)

    def __init__(self, *, seed: int = 3, lookalikes: int = 12, hard_negatives: int = 4, app_lookalikes: int = 5) -> None:
        super().__init__()
        self.seed = seed
        self.n_look = lookalikes
        self.n_neg = hard_negatives
        self.n_apps = app_lookalikes

    def collect(self, brand: BrandQuery, ctx: CollectContext) -> list[Candidate]:
        self.warnings = []
        now = ctx.now()
        rnd = random.Random(f"{self.seed}:{brand.brand_id}:{brand.name}")
        out: list[Candidate] = []
        out.extend(self._official_echoes(brand, now))
        out.extend(self._lookalikes(brand, rnd, now))
        out.extend(self._hard_negatives(brand, rnd, now))
        out.extend(self._app_candidates(brand, rnd, now))
        out.extend(self._web_candidates(brand, now))
        keep = [c for c in out if c.platform in ctx.platforms]
        for c in keep:
            c.source = SOURCE
            c.extras["data_source"] = SOURCE
        return keep

    # ------------------------------------------------------------------
    def _official_echoes(self, brand: BrandQuery, now) -> list[Candidate]:
        out = []
        for platform, handles in sorted(brand.official_handles.items()):
            if platform not in _SOCIAL:
                continue
            for h in handles:
                out.append(
                    Candidate(
                        platform=platform, kind="social", handle=h, display_name=brand.name, profile_url=_profile_url(platform, h),
                        source=SOURCE, fetched_at=now, bio=f"The official {brand.name} page.", followers=250_000, verified=True,
                        created_at=(now - timedelta(days=2400)).date(), extras={"sample_role": "official_echo"},
                    )
                )
        for name, package_id, publisher in brand.official_apps:
            out.append(
                Candidate(
                    platform="google_play", kind="app", handle=package_id, display_name=name,
                    profile_url=f"https://play.google.com/store/apps/details?id={package_id}", source=SOURCE, fetched_at=now,
                    bio=f"Official {brand.name} app.", store="google_play", publisher=publisher, rating=4.4, rating_count=120_000,
                    extras={"sample_role": "official_echo"},
                )
            )
        return out

    def _ranked(self, brand: BrandQuery, seed: int, per_technique: int, ascii_only: bool = False, scam_word_bonus: int = 0) -> list[tuple]:
        """Flagged-looking name mutations, strongest name signal first (ties broken deterministically by a seeded shuffle)."""
        matcher = NameMatcher(brand.name, brand.aliases)
        positives = [c for c in generate_cases(brand.name, seed=seed, per_technique=per_technique) if c.expect_flagged]
        if ascii_only:
            positives = [c for c in positives if c.text.isascii()]
        random.Random(f"{seed}:{brand.brand_id}:rank").shuffle(positives)
        scored = [(matcher.match(c.text), c) for c in positives]
        def strength(m) -> int:
            return m.name_match_pct + (scam_word_bonus if any(w in STRONG_SCAM_WORDS for w in m.added_words) else 0)

        scored.sort(key=lambda t: (t[0].verdict != FLAGGED, -strength(t[0])))
        return scored

    @staticmethod
    def _tiers(n: int) -> list[str]:
        """Tier of each look-alike: a few strong, a few medium, the rest weak (they stay Low)."""
        strong, medium = max(1, round(n * 0.33)), max(1, round(n * 0.25))
        return (["strong"] * strong + ["medium"] * medium + ["weak"] * n)[:n]

    @staticmethod
    def _image(brand: BrandQuery, variant: str, seed: str, extras: dict) -> bytes | None:
        """Generated image for the candidate, or None when the brand has no uploaded logo (then 'logo unavailable' is honest)."""
        data = make_variant(brand.logo_bytes, variant, seed)
        if data is not None:
            extras["logo_image"] = "generated_sample"
            extras["logo_variant"] = variant
        return data

    def _lookalikes(self, brand: BrandQuery, rnd: random.Random, now) -> list[Candidate]:
        ranked = self._ranked(brand, self.seed, 4, scam_word_bonus=15)
        strong_variants = ["near_copy", "resized", "recoloured", "cropped"]
        out: list[Candidate] = []
        used: set[tuple[str, str]] = set()
        tiers = self._tiers(self.n_look)
        own_handles = {re.sub(r"[^a-z0-9]", "", t.lower()) for t in (brand.name, *brand.aliases)}
        i = 0
        for match, case in ranked:
            if len(out) >= len(tiers):
                break
            tier = tiers[len(out)]
            if tier != "strong" and any(w in STRONG_SCAM_WORDS for w in match.added_words):
                continue  # scam words in the name are what lift a candidate into High; keep them for the strong tier
            platform = _SOCIAL[i % len(_SOCIAL)]
            handle = _slug(case.text, platform)
            if handle.replace("_", "").replace(".", "") in own_handles:
                continue  # a look-alike must never collide with the brand's own handle (e.g. "Ac Meb Ank" -> "acmebank")
            if len(handle) < 4 or (platform, handle) in used or not _valid_profile(platform, _profile_url(platform, handle)):
                continue
            used.add((platform, handle))
            i += 1
            extras: dict = {"sample_role": "lookalike", "sample_tier": tier, "technique": case.technique}
            if tier == "strong":  # copied logo, lure bio, brand-new account with the follower pattern of a scam page
                age, bio = rnd.randint(3, 12), _LURES[(0, 1, 4)[len(out) % 3]]
                variant = strong_variants[len(out) % len(strong_variants)]
                stats = {"followers": rnd.randint(20, 400), "following": rnd.randint(2500, 4000), "posts": rnd.randint(0, 4)}
            elif tier == "medium":  # edited logo and a lure bio on a young account
                age, bio = rnd.randint(20, 55), _LURES[(1, 4)[len(out) % 2]]
                variant = "cropped" if len(out) % 2 else "recoloured"
                stats = {"followers": rnd.randint(100, 900), "following": rnd.randint(300, 1500), "posts": rnd.randint(0, 9)}
            else:  # look-alike name only: its own picture, ordinary bio, older account
                age, bio = rnd.randint(150, 600), rnd.choice(_NEUTRAL_BIOS)
                variant = "unrelated"
                stats = {"followers": rnd.randint(200, 3000), "following": rnd.randint(100, 600), "posts": rnd.randint(20, 200)}
            extras["account_age_days"] = age
            image = self._image(brand, variant, f"{brand.brand_id}:{handle}", extras)
            out.append(
                Candidate(
                    platform=platform, kind="social", handle=handle, display_name=case.text.title() if case.text.isascii() else case.text,
                    profile_url=_profile_url(platform, handle), source=SOURCE, fetched_at=now, bio=bio, verified=False,
                    created_at=(now - timedelta(days=age)).date(), extras=extras, inline_image=image, **stats,
                )
            )
        return out

    def _hard_negatives(self, brand: BrandQuery, rnd: random.Random, now) -> list[Candidate]:
        first = brand.name.split()[0]
        suffixes = rnd.sample(BENIGN_SUFFIXES, k=min(self.n_neg, len(BENIGN_SUFFIXES)))
        out = []
        for i, suffix in enumerate(suffixes):
            platform = _SOCIAL[(i + 1) % len(_SOCIAL)]
            name = f"{first} {suffix}"
            handle = _slug(name, platform)
            if len(handle) < 4 or not _valid_profile(platform, _profile_url(platform, handle)):
                continue
            age = rnd.randint(700, 3000)
            extras = {"sample_role": "hard_negative", "account_age_days": age}
            image = self._image(brand, "unrelated", f"{brand.brand_id}:{handle}", extras)
            out.append(
                Candidate(
                    platform=platform, kind="social", handle=handle, display_name=name, profile_url=_profile_url(platform, handle),
                    source=SOURCE, fetched_at=now, bio=rnd.choice(_NEUTRAL_BIOS), followers=rnd.randint(300, 20_000),
                    following=rnd.randint(50, 400), posts=rnd.randint(40, 900), verified=False,
                    created_at=(now - timedelta(days=age)).date(),
                    extras=extras, inline_image=image,
                )
            )
        return out

    def _app_candidates(self, brand: BrandQuery, rnd: random.Random, now) -> list[Candidate]:
        ranked = self._ranked(brand, self.seed + 1, 3, ascii_only=True)
        n = self.n_apps
        tiers = (["strong"] * max(1, round(n * 0.4)) + ["medium"] * max(1, round(n * 0.2)) + ["weak"] * n)[:n]
        out: list[Candidate] = []
        for _match, case in ranked:
            if len(out) >= len(tiers):
                break
            tier = tiers[len(out)]
            slug = re.sub(r"[^a-z0-9]", "", case.text.lower())
            if len(slug) < 4:
                continue
            # strong ones are APK-mirror copies; the others sit in the official stores
            platform = "third_party_apk" if tier == "strong" else ("google_play", "app_store")[len(out) % 2]
            pkg = f"com.{slug}.app"
            app_id = str(rnd.randint(1_000_000_000, 1_999_999_999))
            if platform == "google_play":
                url, store = f"https://play.google.com/store/apps/details?id={pkg}", "google_play"
            elif platform == "app_store":
                url, store = f"https://apps.apple.com/in/app/{slug}/id{app_id}", "apple_app_store"
            else:
                url, store = f"https://apkpure.com/{slug}/{pkg}", "apkpure"
            if not parse_store_url(url).ok:
                continue
            extras: dict = {"sample_role": "lookalike", "sample_tier": tier, "technique": case.technique}
            if platform == "app_store":
                extras["apple_app_id"] = app_id
            if platform == "third_party_apk":
                extras["mirror"] = store
            if tier == "strong":  # brand-new, barely installed mirror copy with the official icon
                extras["installs_count"] = rnd.randint(40, 900)
                extras["released_at"] = (now - timedelta(days=rnd.randint(2, 20))).isoformat()
                variant = "near_copy"
                rating = (round(rnd.uniform(2.1, 3.6), 1), rnd.randint(2, 9))
            elif tier == "medium":  # an edited icon on a barely installed copy
                extras["installs_count"] = rnd.randint(200, 900)
                variant, rating = "recoloured", (round(rnd.uniform(2.1, 3.6), 1), rnd.randint(10, 80))
            else:
                variant, rating = "unrelated", (round(rnd.uniform(3.0, 4.2), 1), rnd.randint(50, 400))
            image = self._image(brand, variant, f"{brand.brand_id}:{pkg}", extras)
            out.append(
                Candidate(
                    platform=platform, kind="app", handle=pkg, display_name=case.text.title(), profile_url=url, source=SOURCE,
                    fetched_at=now, bio=_LURES[0] if tier != "weak" else rnd.choice(_NEUTRAL_BIOS), store=store,
                    publisher=f"{slug[:8].title()} Apps Ltd", rating=rating[0], rating_count=rating[1], extras=extras, inline_image=image,
                )
            )
        return out

    def _web_candidates(self, brand: BrandQuery, now) -> list[Candidate]:
        """Look-alike websites. Every host uses the reserved `.test` TLD (RFC 2606: it can never be registered or resolve), so no
        real website is ever implied. Nothing is fetched, and sample URLs are never sent to a reputation service."""
        label = re.sub(r"[^a-z0-9]", "", brand.name.lower())
        if len(label) < 4:
            return []
        first = re.sub(r"[^a-z0-9]", "", brand.name.split()[0].lower()) or label
        official = brand.domains[0] if brand.domains else f"{label}.example"
        swapped = label[:1] + label[2] + label[1] + label[3:] if len(label) > 3 and label[1] != label[2] else label[:-1]
        homoglyph = label.replace("a", "а", 1) if "a" in label else label.replace("o", "о", 1)
        specs = [
            ("tld_swap", f"https://{label}.test/login", "strong"),
            ("combo", f"https://{label}-support.test/kyc-update", "strong"),
            ("deceptive_subdomain", f"https://{label}.{official}.verify-account.test/", "strong"),
            ("typo", f"https://{swapped}.test/", "medium"),
            ("homoglyph", f"https://{homoglyph}.test/", "medium"),
            ("hard_negative", f"https://{first}-{BENIGN_SUFFIXES[0]}.test/", "weak"),
        ]
        out: list[Candidate] = []
        for technique, url, tier in specs:
            c = candidate_from_web_url(url, source=SOURCE, fetched_at=now,
                                       extras={"sample_role": "hard_negative" if tier == "weak" else "lookalike", "sample_tier": tier, "technique": technique})
            if c is not None and c.handle:
                out.append(c)
        if brand.domains:  # the brand's own site proves the allowlist excludes official domains
            c = candidate_from_web_url(f"https://www.{brand.domains[0]}/", source=SOURCE, fetched_at=now, extras={"sample_role": "official_echo"})
            if c is not None:
                out.append(c)
        return out

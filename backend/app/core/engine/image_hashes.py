"""Additional image hashes beside the home-grown pHash / dHash (logo_similarity.py, whose scores are unchanged).

ImageHash pulls numpy + scipy + PyWavelets (about 200 MB installed), so the four hashes are also implemented here over Pillow only:
phash and dhash are the existing functions, `whash` is a Haar low-pass hash (a simplified wavelet hash: it is NOT bit-identical to
ImageHash.whash) and `colorhash` is a hue / saturation histogram. `ImageHashSimilarity` uses the real library when it is installed
(optional, lazily imported). Hash similarity is a hint that two pictures look alike; it is not proof that one was copied from the other.

Algorithm version "image-2": 0.4 phash + 0.3 dhash + 0.3 whash. The colour hash is reported separately (a recoloured copy is still a copy).
It is NOT the default backend: IMAGE_SIMILARITY_BACKEND=multihash selects it, so existing scores (algorithm "image-1") do not move.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PIL import Image

from .logo_similarity import NotAvailable, _hash_score, _to_gray, dhash, hamming, phash

ALGORITHM = "image-2"
WEIGHTS = (0.4, 0.3, 0.3)


def whash(img: Image.Image) -> int:
    """64-bit Haar low-pass hash: the 8x8 block means (the Haar LL band) of a contrast-normalised image against their median."""
    g = _to_gray(img, (64, 64)).resize((8, 8), Image.Resampling.BOX)
    px = list(g.getdata())
    median = sorted(px)[len(px) // 2 - 1 : len(px) // 2 + 1]
    med = sum(median) / 2
    bits = 0
    for i, v in enumerate(px):
        if v > med:
            bits |= 1 << i
    return bits


def colorhash(img: Image.Image) -> list[int]:
    """14 fractions (percent): black, gray, then 6 hue bins for faint and 6 for bright colours."""
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        img = bg
    hsv = img.convert("RGB").resize((64, 64), Image.Resampling.BOX).convert("HSV")
    bins = [0] * 14
    px = list(hsv.getdata())
    for h, s, v in px:
        if v < 32:
            bins[0] += 1
        elif s < 85:
            bins[1] += 1
        else:
            hue = min(5, int(h * 6 / 256))
            bins[2 + hue + (6 if s >= 170 else 0)] += 1
    n = len(px)
    return [round(100 * b / n) for b in bins]


def compute_hashes(img: Image.Image) -> dict[str, Any]:
    return {"phash": f"{phash(img):016x}", "dhash": f"{dhash(img):016x}", "whash": f"{whash(img):016x}", "colorhash": colorhash(img)}


def compare_hashes(a: dict[str, Any], b: dict[str, Any]) -> dict[str, float]:
    out = {k: round(_hash_score(int(a[k], 16), int(b[k], 16)), 1) for k in ("phash", "dhash", "whash")}
    l1 = sum(abs(x - y) for x, y in zip(a["colorhash"], b["colorhash"], strict=True))
    out["colorhash"] = round(max(0.0, 100 - l1 / 2), 1)
    return out


def combine(components: dict[str, float]) -> float:
    wp, wd, ww = WEIGHTS
    return round(wp * components["phash"] + wd * components["dhash"] + ww * components["whash"], 1)


class MultiHashSimilarity:
    """ImageSimilarity (algorithm image-2): phash + dhash + whash computed locally."""

    def components(self, a: Image.Image, b: Image.Image) -> dict[str, float]:
        return compare_hashes(compute_hashes(a), compute_hashes(b))

    def similarity(self, a: Image.Image, b: Image.Image) -> float:
        return combine(self.components(a, b))


class ImageHashSimilarity:
    """The same weighting with the ImageHash library's phash / dhash / whash. Optional: raises NotAvailable when it is not installed."""

    def __init__(self) -> None:
        try:
            import imagehash  # type: ignore[import-not-found]  # lazy: pulls numpy, scipy, PyWavelets
        except ImportError as exc:
            raise NotAvailable("The optional package 'ImageHash' is not installed (pip install -r requirements-extra.txt).") from exc
        self._ih = imagehash

    def components(self, a: Image.Image, b: Image.Image) -> dict[str, float]:
        ih = self._ih
        out = {}
        for name, fn in (("phash", ih.phash), ("dhash", ih.dhash), ("whash", ih.whash)):
            sim = 1 - (fn(a.convert("RGB")) - fn(b.convert("RGB"))) / 64
            out[name] = round(max(0.0, (sim - 0.5) / 0.5) * 100, 1)
        out["colorhash"] = round(100 * (1 - (ih.colorhash(a.convert("RGB")) - ih.colorhash(b.convert("RGB"))) / 42), 1)
        return out

    def similarity(self, a: Image.Image, b: Image.Image) -> float:
        return combine(self.components(a, b))


def best_match(candidate: Image.Image, officials: Sequence[tuple[str, Image.Image]], impl: Any) -> tuple[float, str | None]:
    """Highest similarity against ANY official logo, and which one it was (its label)."""
    best, who = 0.0, None
    for label, img in officials:
        s = impl.similarity(candidate, img)
        if s > best or who is None:
            best, who = s, label
    return best, who


__all__ = ["ALGORITHM", "ImageHashSimilarity", "MultiHashSimilarity", "best_match", "colorhash", "compare_hashes", "compute_hashes", "hamming", "whash"]

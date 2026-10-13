"""Logo / icon similarity from perceptual hashes (pHash + dHash), implemented over Pillow.

Score 0-100. Hash similarity is `1 - hamming/64`; two unrelated images sit near 0.5 (random bits), so the
raw similarity is rescaled with `(sim - 0.5) / 0.5` and clamped at 0. The final score is a weighted mean
of the pHash and dHash scores (pHash is robust to resize/recolor/JPEG; dHash catches gradient structure).

Thresholds (see `docs/DECISIONS.md` and tests/engine/test_logo_similarity.py):
    LIKELY_COPY_MIN  = 80  -> copy, resize, grayscale, recolor, JPEG recompress (q30) all measured >= 80
    SIMILAR_MIN      = 55  -> slight crops / light edits (3% crop measured 69-80+); worth a human look
    below SIMILAR_MIN        -> unrelated (300 random unrelated pairs: median 6, max 39), no points
Known gaps: colour-inverted logos score ~0 and larger crops (>=10%) fall below SIMILAR_MIN. The numbers come
from synthetic shape logos, not real brand logos: re-tune on labelled data. A CLIP-style embedder can replace the
hashes through the `ImageSimilarity` protocol (cosine similarity scaled 0-100).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

from PIL import Image, ImageOps

LIKELY_COPY_MIN = 80.0
SIMILAR_MIN = 55.0

_HASH_BITS = 64
_DCT_SIZE = 32
_LOW = 8


class NotAvailable(RuntimeError):
    """An optional similarity backend (e.g. CLIP) is not installed."""


@runtime_checkable
class ImageSimilarity(Protocol):
    def similarity(self, a: Image.Image, b: Image.Image) -> float:
        """Return 0-100 (100 = identical)."""
        ...


def _to_gray(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        img = bg
    gray = img.convert("L")
    gray = ImageOps.autocontrast(gray)  # makes recolored / low-contrast copies comparable
    return gray.resize(size, Image.Resampling.LANCZOS)


def _dct_matrix(n: int) -> list[list[float]]:
    return [[math.cos(math.pi * (2 * x + 1) * u / (2 * n)) * (math.sqrt(1 / n) if u == 0 else math.sqrt(2 / n)) for x in range(n)] for u in range(n)]


_C = _dct_matrix(_DCT_SIZE)


def phash(img: Image.Image) -> int:
    """64-bit perceptual hash: 2-D DCT of a 32x32 grayscale image, low 8x8 block vs its median."""
    g = _to_gray(img, (_DCT_SIZE, _DCT_SIZE))
    px = list(g.getdata())
    rows = [px[r * _DCT_SIZE : (r + 1) * _DCT_SIZE] for r in range(_DCT_SIZE)]
    # only the first _LOW frequency rows/cols are needed: T = C[:LOW] * X * C[:LOW]^T
    cl = _C[:_LOW]
    tmp = [[sum(cl[u][k] * rows[k][x] for k in range(_DCT_SIZE)) for x in range(_DCT_SIZE)] for u in range(_LOW)]
    block = [[sum(tmp[u][x] * cl[v][x] for x in range(_DCT_SIZE)) for v in range(_LOW)] for u in range(_LOW)]
    flat = [block[u][v] for u in range(_LOW) for v in range(_LOW)]
    median = sorted(flat[1:])[(len(flat) - 1) // 2]  # median without the DC term
    bits = 0
    for i, val in enumerate(flat):
        if val > median:
            bits |= 1 << i
    return bits


def dhash(img: Image.Image) -> int:
    """64-bit difference hash: 9x8 grayscale, left pixel brighter than right neighbour."""
    g = _to_gray(img, (9, 8))
    px = list(g.getdata())
    bits = 0
    i = 0
    for r in range(8):
        for c in range(8):
            if px[r * 9 + c] > px[r * 9 + c + 1]:
                bits |= 1 << i
            i += 1
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _hash_score(a: int, b: int) -> float:
    sim = 1 - hamming(a, b) / _HASH_BITS
    return max(0.0, (sim - 0.5) / 0.5) * 100


class HashSimilarity:
    """Default ImageSimilarity: weighted pHash + dHash."""

    def __init__(self, phash_weight: float = 0.6) -> None:
        self.wp = phash_weight

    def similarity(self, a: Image.Image, b: Image.Image) -> float:
        return round(self.wp * _hash_score(phash(a), phash(b)) + (1 - self.wp) * _hash_score(dhash(a), dhash(b)), 1)


def logo_similarity(a: Image.Image, b: Image.Image) -> float:
    return HashSimilarity().similarity(a, b)


def best_logo_similarity(candidate: Image.Image, official: Sequence[Image.Image], impl: ImageSimilarity | None = None) -> float:
    """Highest similarity of `candidate` against any official logo."""
    impl = impl or HashSimilarity()
    return max((impl.similarity(candidate, o) for o in official), default=0.0)


class ClipSimilarity:
    """CLIP-style embedding cosine similarity, scaled 0-100. Optional and lazily imported.

    Pass `embedder` (image -> vector) to use any backend (also how tests exercise it); without one it
    tries `open_clip` + `torch` and raises NotAvailable when they are not installed.
    """

    def __init__(self, embedder: Callable[[Image.Image], Sequence[float]] | None = None, model: str = "ViT-B-32", pretrained: str = "openai") -> None:
        if embedder is None:
            embedder = self._load_open_clip(model, pretrained)
        self._embed = embedder

    @staticmethod
    def _load_open_clip(model_name: str, pretrained: str) -> Callable[[Image.Image], Sequence[float]]:
        try:
            import open_clip  # type: ignore[import-not-found]
            import torch  # type: ignore[import-not-found]
        except ImportError as exc:
            raise NotAvailable("CLIP similarity needs the optional packages 'open_clip_torch' and 'torch'.") from exc
        model, _, preprocess = open_clip.create_model_and_transforms(model_name, pretrained=pretrained)  # pragma: no cover
        model.eval()  # pragma: no cover

        def embed(img: Image.Image) -> Sequence[float]:  # pragma: no cover
            with torch.no_grad():
                vec = model.encode_image(preprocess(img.convert("RGB")).unsqueeze(0))[0]
            return vec.tolist()

        return embed  # pragma: no cover

    def similarity(self, a: Image.Image, b: Image.Image) -> float:
        va, vb = list(self._embed(a)), list(self._embed(b))
        na, nb = math.sqrt(sum(x * x for x in va)), math.sqrt(sum(x * x for x in vb))
        if na == 0 or nb == 0:
            return 0.0
        cos = sum(x * y for x, y in zip(va, vb, strict=True)) / (na * nb)
        return round(max(0.0, cos) * 100, 1)


def load_image(data: Any) -> Image.Image:
    """bytes or file-like -> RGBA/RGB PIL image (decoded fully)."""
    import io

    img = Image.open(io.BytesIO(data) if isinstance(data, bytes | bytearray) else data)
    img.load()
    return img

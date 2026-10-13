"""Generated images for the SAMPLE collector only.

When the brand has uploaded a logo, sample look-alikes get an in-memory image derived from it, so the normal
logo comparison runs without any network fetch and the whole scoring range is reachable in sample mode:

    near_copy  full-size, near-lossless JPEG re-save (what a re-uploaded copy of the file looks like)
    resized    downscaled and saved as PNG
    recoloured slightly shifted colours
    cropped    ~3% trimmed off every edge
    unrelated  a deterministic generated image that has nothing to do with the logo (hard negatives, weak look-alikes)

Everything is derived from the user's own logo or generated from a seed. Nothing is downloaded and no real
third-party artwork is used. The pipeline honours these bytes ONLY for candidates whose source is "sample".
"""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw, ImageEnhance

VARIANTS = ("near_copy", "resized", "recoloured", "cropped", "unrelated")
_SIDE = 256


def _open(logo: bytes) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(logo))
        img.load()
    except Exception:  # noqa: BLE001 - a broken logo just means "no sample images"
        return None
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        img = bg
    return img.convert("RGB")


def _encode(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def unrelated_image(seed: str) -> bytes:
    """A deterministic abstract image (random blocks and discs) that is not derived from any logo."""
    rnd = random.Random(f"unrelated:{seed}")
    img = Image.new("RGB", (_SIDE, _SIDE), tuple(rnd.randrange(30, 230) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(rnd.randint(5, 9)):
        x0, y0 = rnd.randrange(0, _SIDE - 40), rnd.randrange(0, _SIDE - 40)
        x1, y1 = x0 + rnd.randrange(30, 120), y0 + rnd.randrange(30, 120)
        fill = tuple(rnd.randrange(0, 256) for _ in range(3))
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)((x0, y0, x1, y1), fill=fill)
    return _encode(img, "PNG")


def make_variant(logo: bytes | None, variant: str, seed: str) -> bytes | None:
    """Bytes for one variant, or None when there is no usable uploaded logo (so 'logo unavailable' stays honest)."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown sample image variant: {variant}")
    if not logo:
        return None
    img = _open(logo)
    if img is None:
        return None
    if variant == "unrelated":
        return unrelated_image(seed)
    if variant == "near_copy":
        return _encode(img, "JPEG", quality=100, subsampling=0)
    img = img.resize((_SIDE, _SIDE), Image.Resampling.LANCZOS)
    if variant == "resized":
        return _encode(img.resize((96, 96), Image.Resampling.LANCZOS), "PNG")
    if variant == "recoloured":
        shifted = ImageEnhance.Color(img).enhance(0.8)
        r, g, b = shifted.split()
        return _encode(Image.merge("RGB", (r.point(lambda v: min(255, v + 12)), g, b.point(lambda v: max(0, v - 12)))), "PNG")
    m = round(_SIDE * 0.03)  # cropped
    return _encode(img.crop((m, m, _SIDE - m, _SIDE - m)).resize((_SIDE, _SIDE), Image.Resampling.LANCZOS), "PNG")

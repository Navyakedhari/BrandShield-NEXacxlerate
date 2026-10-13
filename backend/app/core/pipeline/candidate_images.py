"""Safe upload of candidate images and app icons.

The type comes from the bytes (Pillow), never from the filename or the client's Content-Type. Size, dimension and pixel caps (decompression-bomb
guard) apply; the image is decoded, then RE-ENCODED (metadata and appended bytes are dropped) and stored under a name derived from its hash.
The original filename is never stored, used or served. The image is never executed; it is served only with a fixed image/* type + nosniff."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.engine.image_hashes import compute_hashes
from app.core.pipeline.logo import _FORMATS, LogoError, sniff_format

MAX_PIXELS = 25_000_000
MIN_SIDE = 32
MAX_SIDE = 6000


@dataclass
class ProcessedImage:
    data: bytes
    format: str
    media_type: str
    width: int
    height: int
    sha256: str
    hashes: dict


def process_candidate_image(data: bytes, max_bytes: int) -> ProcessedImage:
    if len(data) > max_bytes:
        raise LogoError(413, f"Image is larger than {max_bytes // (1024 * 1024) or 1} MB")
    if not data or sniff_format(data[:16]) is None:
        raise LogoError(415, "Image must be a PNG, JPEG or WebP picture (SVG and other types are refused)")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.load()
            w, h = img.size
            if fmt not in _FORMATS:
                raise LogoError(415, "Image must be a PNG, JPEG or WebP picture")
            if w < MIN_SIDE or h < MIN_SIDE:
                raise LogoError(422, f"Image must be at least {MIN_SIDE} x {MIN_SIDE} pixels")
            if w > MAX_SIDE or h > MAX_SIDE:
                raise LogoError(422, f"Image must be at most {MAX_SIDE} x {MAX_SIDE} pixels")
            out = io.BytesIO()
            (img.convert("RGB") if fmt == "JPEG" and img.mode not in ("RGB", "L") else img).save(out, format=fmt)
            hashes = compute_hashes(img)
    except LogoError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise LogoError(415, "Image is not a valid PNG, JPEG or WebP picture") from exc
    clean = out.getvalue()
    ext, media = _FORMATS[fmt]
    return ProcessedImage(clean, ext, media, w, h, hashlib.sha256(clean).hexdigest(), hashes)


def save_candidate_image(data_dir: Path, brand_id: int, img: ProcessedImage) -> str:
    rel = Path("candidate_images") / f"brand_{brand_id}" / f"{img.sha256[:24]}.{img.format}"
    dest = data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(img.data)
    return rel.as_posix()

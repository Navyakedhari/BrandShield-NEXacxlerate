"""Brand logo validation and storage. Type is decided from the bytes, never from the filename."""
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MAX_PIXELS = 25_000_000  # decompression-bomb guard
MIN_SIDE = 256
MIN_ASPECT, MAX_ASPECT = 0.8, 1.25
_FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}


class LogoError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class ProcessedLogo:
    data: bytes
    format: str  # png | jpg | webp
    media_type: str
    width: int
    height: int
    sha256: str


def sniff_format(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def process_logo(data: bytes, max_bytes: int) -> ProcessedLogo:
    if len(data) > max_bytes:
        raise LogoError(413, "Logo is larger than 2 MB")
    if not data or sniff_format(data[:16]) is None:
        raise LogoError(415, "Logo must be a PNG, JPEG or WebP image")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.load()
            width, height = img.size
            if fmt not in _FORMATS:
                raise LogoError(415, "Logo must be a PNG, JPEG or WebP image")
            if width < MIN_SIDE or height < MIN_SIDE:
                raise LogoError(422, f"Logo must be at least {MIN_SIDE} x {MIN_SIDE} pixels")
            if not (MIN_ASPECT <= width / height <= MAX_ASPECT):
                raise LogoError(422, "Logo must be roughly square")
            # Re-encode: drops metadata and any bytes appended after the image.
            out = io.BytesIO()
            save_img = img.convert("RGB") if fmt == "JPEG" and img.mode not in ("RGB", "L") else img
            save_img.save(out, format=fmt)
    except LogoError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise LogoError(415, "Logo must be a valid PNG, JPEG or WebP image") from exc
    clean = out.getvalue()
    ext, media = _FORMATS[fmt]
    return ProcessedLogo(clean, ext, media, width, height, hashlib.sha256(clean).hexdigest())


def save_logo(data_dir: Path, brand_id: int, logo: ProcessedLogo) -> str:
    """Write under data_dir/logos/brand_<id>/; the filename is derived from the hash, never user input."""
    rel = Path("logos") / f"brand_{brand_id}" / f"{logo.sha256[:24]}.{logo.format}"
    dest = data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(logo.data)
    return rel.as_posix()

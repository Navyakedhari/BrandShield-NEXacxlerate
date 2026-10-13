import io
import random

import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from app.core.engine.logo_similarity import (
    LIKELY_COPY_MIN,
    SIMILAR_MIN,
    ClipSimilarity,
    HashSimilarity,
    ImageSimilarity,
    NotAvailable,
    best_logo_similarity,
    dhash,
    hamming,
    load_image,
    logo_similarity,
    phash,
)


def make_logo(seed: int, size: int = 256) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (size, size), tuple(rnd.randint(0, 255) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(5):
        fg = tuple(rnd.randint(0, 255) for _ in range(3))
        x0, y0 = rnd.randint(0, size // 2), rnd.randint(0, size // 2)
        x1, y1 = rnd.randint(size // 2, size), rnd.randint(size // 2, size)
        kind = rnd.choice(["rect", "ellipse", "poly"])
        if kind == "rect":
            d.rectangle([x0, y0, x1, y1], fill=fg)
        elif kind == "ellipse":
            d.ellipse([x0, y0, x1, y1], fill=fg)
        else:
            d.polygon([(x0, y1), (x1, y1), ((x0 + x1) // 2, y0)], fill=fg)
    return img


def jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf)


def crop(img: Image.Image, frac: float) -> Image.Image:
    w, h = img.size
    return img.crop((int(w * frac), int(h * frac), w, h)).resize((w, h))


SEEDS = range(12)


def test_exact_copy_is_100():
    for s in SEEDS:
        a = make_logo(s)
        assert logo_similarity(a, a.copy()) == 100.0


@pytest.mark.parametrize(
    "name,fn",
    [
        ("resize_64", lambda a: a.resize((64, 64))),
        ("resize_up", lambda a: a.resize((512, 512))),
        ("grayscale", ImageOps.grayscale),
        ("desaturate", lambda a: ImageEnhance.Color(a).enhance(0.3)),
        ("jpeg_q30", lambda a: jpeg(a, 30)),
        ("jpeg_q70", lambda a: jpeg(a, 70)),
    ],
)
def test_benign_transforms_stay_above_copy_threshold(name, fn):
    for s in SEEDS:
        a = make_logo(s)
        assert logo_similarity(a, fn(a)) >= LIKELY_COPY_MIN, (name, s)


def test_slight_crop_stays_above_similar_threshold():
    scores = [logo_similarity(make_logo(s), crop(make_logo(s), 0.03)) for s in SEEDS]
    assert min(scores) >= SIMILAR_MIN


def test_unrelated_images_stay_below_similar_threshold():
    scores = [logo_similarity(make_logo(s), make_logo(s + 1000)) for s in range(60)]
    assert max(scores) < SIMILAR_MIN
    assert sorted(scores)[len(scores) // 2] < 25  # typical unrelated pair is far lower


def test_score_ordering():
    a = make_logo(3)
    other = make_logo(1003)
    copy = logo_similarity(a, a.copy())
    resized = logo_similarity(a, a.resize((96, 96)))
    cropped = logo_similarity(a, crop(a, 0.06))
    unrelated = logo_similarity(a, other)
    assert copy >= resized > cropped > unrelated


def test_transparent_png_composited_on_white():
    a = make_logo(5)
    rgba = a.convert("RGBA")
    assert logo_similarity(a, rgba) == 100.0
    clear = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    assert logo_similarity(clear, Image.new("RGB", (64, 64), "white")) >= 0


def test_hashes_are_64_bit_and_deterministic():
    a = make_logo(2)
    assert 0 <= phash(a) < 2**64 and 0 <= dhash(a) < 2**64
    assert phash(a) == phash(a.copy()) and dhash(a) == dhash(a.copy())
    assert hamming(0b1011, 0b0001) == 2


def test_best_of_several_and_protocol():
    a = make_logo(7)
    refs = [make_logo(2007), a.resize((128, 128)), make_logo(2008)]
    assert best_logo_similarity(a, refs) >= LIKELY_COPY_MIN
    assert best_logo_similarity(a, []) == 0.0
    assert isinstance(HashSimilarity(), ImageSimilarity)


def test_load_image_from_bytes():
    buf = io.BytesIO()
    make_logo(1).save(buf, "PNG")
    img = load_image(buf.getvalue())
    assert img.size == (256, 256)


def test_clip_missing_package_raises_not_available(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "open_clip", None)  # makes `import open_clip` fail
    with pytest.raises(NotAvailable):
        ClipSimilarity()


def test_clip_with_injected_embedder_swaps_in():
    vecs = {16: [1.0, 0.0], 32: [0.0, 1.0], 48: [1.0, 1.0]}
    clip = ClipSimilarity(embedder=lambda img: vecs[img.size[0]])
    assert isinstance(clip, ImageSimilarity)
    a, b, c = (Image.new("RGB", (s, s)) for s in (16, 32, 48))
    assert clip.similarity(a, a) == 100.0
    assert clip.similarity(a, b) == 0.0
    assert 70 < clip.similarity(a, c) < 72
    zero = ClipSimilarity(embedder=lambda img: [0.0, 0.0])
    assert zero.similarity(a, b) == 0.0

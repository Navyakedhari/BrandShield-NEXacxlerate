"""E6: extra image hashes, multi-logo best match, safe candidate image upload. Generated images only; no network."""

import io
import random

import pytest
from PIL import Image, ImageDraw, ImageOps

from app.core.engine.image_hashes import ImageHashSimilarity, MultiHashSimilarity, best_match, compute_hashes
from app.core.engine.logo_similarity import HashSimilarity, NotAvailable
from app.core.pipeline.candidate_images import process_candidate_image
from app.core.pipeline.logo import LogoError
from tests.conftest import ADMIN, headers_for
from tests.scan_helpers import make_client, set_mode, setup_brand


def logo(seed=1, size=300) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (size, size), (250, 250, 250))
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x, y = rnd.randint(10, size - 120), rnd.randint(10, size - 120)
        w, h = rnd.randint(40, 110), rnd.randint(30, 110)
        col = tuple(rnd.randint(0, 200) for _ in range(3))
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)([x, y, x + w, y + h], fill=col)
    return img


def enc(img, fmt="PNG") -> bytes:
    out = io.BytesIO()
    img.save(out, format=fmt)
    return out.getvalue()


def recolor(img):
    r, g, b = img.split()
    return Image.merge("RGB", (b, r, g))


IMPLS = [HashSimilarity(), MultiHashSimilarity()]


@pytest.mark.parametrize("impl", IMPLS, ids=["image-1", "image-2"])
def test_copy_resize_recolor_vs_unrelated(impl):
    a = logo(1)
    assert impl.similarity(a, a.copy()) >= 99
    assert impl.similarity(a, a.resize((150, 150))) >= 80
    assert impl.similarity(a, recolor(a)) >= 55  # structure survives a colour shuffle
    assert impl.similarity(a, a.crop((6, 6, 294, 294)).resize((300, 300))) >= 55
    assert impl.similarity(a, logo(2)) < 55
    assert impl.similarity(a, logo(3)) < 55


def test_flip_is_not_reported_as_a_copy():
    a = logo(1)
    assert MultiHashSimilarity().similarity(a, ImageOps.mirror(a)) < 80


def test_components_and_colorhash():
    a = logo(1)
    comp = MultiHashSimilarity().components(a, recolor(a))
    assert set(comp) == {"phash", "dhash", "whash", "colorhash"} and comp["colorhash"] < 100 == MultiHashSimilarity().components(a, a)["colorhash"]
    assert len(compute_hashes(a)["colorhash"]) == 14


def test_default_backend_scores_unchanged_by_new_module():
    a, b = logo(1), logo(1).resize((200, 200))
    assert HashSimilarity().similarity(a, b) == HashSimilarity().similarity(a, b)  # image-1 is the untouched home-grown pHash+dHash


def test_imagehash_adapter_matches_when_installed():
    try:
        impl = ImageHashSimilarity()
    except NotAvailable:
        pytest.skip("ImageHash not installed")
    a = logo(1)
    assert impl.similarity(a, a.copy()) >= 99 and impl.similarity(a, logo(2)) < 55


def test_best_match_reports_which_logo():
    cand = logo(2).resize((180, 180))
    sim, who = best_match(cand, [("blue", logo(1)), ("red", logo(2)), ("other", logo(3))], HashSimilarity())
    assert who == "red" and sim >= 80


# ---- safe upload -----------------------------------------------------------------------------------------


def test_upload_processing_rules():
    ok = process_candidate_image(enc(logo(1)) + b"TRAILING-BYTES", 2_000_000)
    assert ok.format == "png" and b"TRAILING" not in ok.data and len(ok.sha256) == 64 and ok.hashes["phash"]
    assert process_candidate_image(enc(logo(1), "JPEG"), 2_000_000).format == "jpg"
    with pytest.raises(LogoError) as e:
        process_candidate_image(enc(Image.new("RGB", (10, 10))), 2_000_000)  # tiny
    assert e.value.status_code == 422
    with pytest.raises(LogoError) as e:
        process_candidate_image(enc(logo(1))[:200], 2_000_000)  # corrupt
    assert e.value.status_code == 415
    with pytest.raises(LogoError) as e:
        process_candidate_image(b"x" * 3_000_000, 2_000_000)  # oversized
    assert e.value.status_code == 413
    for bad in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", b"GIF89a" + b"0" * 100, b"%PDF-1.4 ..."):
        with pytest.raises(LogoError) as e:
            process_candidate_image(bad, 2_000_000)  # wrong type
        assert e.value.status_code == 415


def test_decompression_bomb_is_refused():
    big = Image.new("L", (6001, 1), 0)
    with pytest.raises(LogoError):
        process_candidate_image(enc(big), 2_000_000)  # too wide
    bomb = Image.new("1", (7000, 7000), 0)  # 49 MP, compresses to a few KB
    data = enc(bomb)
    assert len(data) < 200_000
    with pytest.raises(LogoError):
        process_candidate_image(data, 2_000_000)


def test_upload_endpoint_ignores_filename_and_content_type(settings, checker):
    with make_client(settings, checker, []) as c:
        h = headers_for(c, *ADMIN)
        setup_brand(c, h, accounts=(), apps=())
        r = c.post("/api/candidate-images", files={"file": ("../../evil.php", enc(logo(1)), "text/html")}, headers=h)
        assert r.status_code == 201, r.text
        body = r.json()
        assert "evil" not in str(body) and "not proof" in body["note"]
        got = c.get(body["url"], headers=h)
        assert got.headers["content-type"] == "image/png" and got.headers["x-content-type-options"] == "nosniff"
        again = c.post("/api/candidate-images", files={"file": ("x.png", enc(logo(1)), "image/png")}, headers=h)
        assert again.json()["id"] == body["id"]  # same bytes: one row
        assert c.post("/api/candidate-images", files={"file": ("a.svg", b"<svg/>", "image/svg+xml")}, headers=h).status_code == 415
        assert c.post("/api/candidate-images", files={"file": ("a.png", b"x" * 3_000_000, "image/png")}, headers=h).status_code == 413


def test_multi_logo_best_match_is_recorded_on_a_manual_candidate(settings, checker):
    with make_client(settings, checker, []) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        b = setup_brand(c, h, accounts=(), apps=())
        p1 = c.post(f"/api/brands/{b['id']}/logo", files={"file": ("l.png", enc(logo(1)), "image/png")}, headers=h)
        assert p1.status_code == 200
        r2 = c.post(f"/api/brands/{b['id']}/logos?label=Dark%20variant", files={"file": ("l2.png", enc(logo(2)), "image/png")}, headers=h)
        assert r2.status_code == 201 and r2.json()["label"] == "Dark variant"
        assert c.post(f"/api/brands/{b['id']}/logos", files={"file": ("l2.png", enc(logo(2)), "image/png")}, headers=h).status_code == 409
        img = c.post("/api/candidate-images", files={"file": ("c.png", enc(logo(2).resize((200, 200))), "image/png")}, headers=h).json()
        r = c.post("/api/candidates/social", json={"platform": "instagram", "handle": "acme_bank_help", "display_name": "Acme Bank Help",
                                                    "image_id": img["id"]}, headers=h)
        assert r.status_code == 201, r.text
        d = c.get(f"/api/detections/{r.json()['detection_id']}", headers=h).json()
        assert d["logo"]["status"] == "compared" and d["logo"]["matched_logo"] == "Dark variant" and d["logo"]["similarity"] >= 80
        assert "not proof" in d["logo"]["note"] and d["origin"] == "manual"

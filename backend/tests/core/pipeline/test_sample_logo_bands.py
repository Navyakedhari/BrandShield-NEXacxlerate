"""Sample mode: generated logo variants make every score band reachable, honestly and without any network use."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.collectors.base import BrandQuery, CollectContext
from app.core.collectors.sample import SampleCollector
from app.core.collectors.sample_images import VARIANTS, make_variant
from app.core.engine.logo_similarity import LIKELY_COPY_MIN, SIMILAR_MIN, load_image, logo_similarity
from app.core.pipeline.image_fetch import SafeFetcher
from scripts.seed_test_brand import make_logo
from tests.conftest import ADMIN, headers_for
from tests.scan_helpers import StaticCollector, all_detections, make_client, run_scan, set_mode, setup_brand, social

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
LOGO = make_logo()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("sample mode must not fetch anything")

    monkeypatch.setattr(SafeFetcher, "fetch_image", boom)
    monkeypatch.setattr(SafeFetcher, "fetch_bytes", boom)


def _brand_with_logo(client, h):
    b = setup_brand(client, h)
    r = client.post(f"/api/brands/{b['id']}/logo", files={"file": ("logo.png", LOGO, "image/png")}, headers=h)
    assert r.status_code == 200, r.text
    return b


def _sample_scan(settings, checker, *, with_logo=True):
    ctx = make_client(settings, checker, [SampleCollector()])
    client = ctx.__enter__()
    h = headers_for(client, *ADMIN)
    set_mode(client, h, "sample")
    b = _brand_with_logo(client, h) if with_logo else setup_brand(client, h)
    scan = run_scan(client, h)
    return ctx, client, h, b, scan


def _social_and_apps(client, h):
    """Web (look-alike domain) detections carry no logo; the logo/band tests are about social profiles and apps."""
    return [i for i in all_detections(client, h) if i["category"] != "web"]


def _details(client, h, items):
    return [client.get(f"/api/detections/{i['id']}", headers=h).json() for i in items]


# ---------------------------------------------------------------------------------------------- collector


def test_variants_are_deterministic_and_have_the_intended_similarity():
    official = load_image(LOGO)
    sims = {}
    for v in VARIANTS:
        data = make_variant(LOGO, v, "seed")
        assert data == make_variant(LOGO, v, "seed")
        sims[v] = logo_similarity(load_image(data), official)
    assert sims["near_copy"] >= LIKELY_COPY_MIN
    assert all(sims[v] >= SIMILAR_MIN for v in ("resized", "recoloured", "cropped"))
    assert sims["unrelated"] < SIMILAR_MIN
    assert make_variant(LOGO, "unrelated", "a") != make_variant(LOGO, "unrelated", "b")
    assert make_variant(None, "near_copy", "seed") is None and make_variant(b"not an image", "near_copy", "seed") is None
    with pytest.raises(ValueError):
        make_variant(LOGO, "bogus", "seed")


def test_collector_images_deterministic_and_skipped_without_a_logo():
    brand = BrandQuery(brand_id=1, name="Acme Bank", aliases=("Acme",), logo_bytes=LOGO)
    ctx = CollectContext(now=lambda: NOW)
    a, b = SampleCollector().collect(brand, ctx), SampleCollector().collect(brand, ctx)
    assert [c.inline_image for c in a] == [c.inline_image for c in b]
    withimg = [c for c in a if c.inline_image]
    assert withimg and all(c.extras["logo_image"] == "generated_sample" and c.extras["logo_variant"] in VARIANTS for c in withimg)
    assert all(c.inline_image for c in a if c.extras["sample_role"] in ("lookalike", "hard_negative"))
    assert not any(c.inline_image for c in a if c.extras["sample_role"] == "official_echo")
    bare = SampleCollector().collect(BrandQuery(brand_id=1, name="Acme Bank", aliases=("Acme",)), ctx)
    assert not any(c.inline_image for c in bare) and not any("logo_image" in c.extras for c in bare)
    # the bytes are never serialized into cache / evidence payloads
    assert all("inline_image" not in c.to_dict() for c in a)


# ---------------------------------------------------------------------------------------------- pipeline


def test_sample_scan_reaches_every_band_with_recorded_logo_comparison(settings, checker):
    ctx, c, h, _b, scan = _sample_scan(settings, checker)
    try:
        assert scan["counts"]["logo_compared"] > 0 and scan["counts"]["logo_unavailable"] == 0
        items = _social_and_apps(c, h)
        bands = {band: [i for i in items if i["risk_band"] == band] for band in ("high", "medium", "low")}
        assert all(i["source"] == "sample" for i in items)
        assert len(bands["high"]) >= 4 and len(bands["medium"]) >= 3 and len(bands["low"]) >= 3
        assert any(i["platform"] in ("instagram", "facebook", "x", "telegram", "linkedin") for i in bands["high"])
        assert any(i["platform"] in ("third_party_apk", "google_play", "app_store") for i in bands["high"])
        assert all(i["score"] >= 85 for i in bands["high"]) and all(70 <= i["score"] < 85 for i in bands["medium"])
        top = _details(c, h, [max(items, key=lambda i: i["score"])])[0]
        assert top["logo"]["status"] == "compared" and top["logo"]["origin"] == "generated_sample"
        assert top["logo"]["similarity"] >= LIKELY_COPY_MIN
        assert any(r["signal"] == "logo_similarity" and r["points"] > 0 for r in top["reasons"])
        # the evidence snapshot records that the image was generated, not downloaded
        bundle = c.get(f"/api/detections/{top['id']}/evidence", headers=h).json()
        assert bundle["hash_verified"] is True
        snap = bundle["snapshot"]
        assert snap["extras"]["logo_image"] == "generated_sample" and snap["extras"]["data_source"] == "sample"
        assert "inline_image" not in snap
        # every detection says what it was compared with
        for d in _details(c, h, items):
            assert d["logo"]["status"] == "compared" and d["is_sample"] is True
    finally:
        ctx.__exit__(None, None, None)


def test_sample_scan_excludes_official_echoes_and_does_not_flag_hard_negatives(settings, checker):
    ctx, c, h, _b, scan = _sample_scan(settings, checker)
    try:
        assert scan["counts"]["official_excluded"] == 4  # 2 accounts + 1 app + 1 official domain (the web echo)
        names = {i["name"].lower() for i in _social_and_apps(c, h)}
        assert "acmebank" not in {i["handle"] for i in _social_and_apps(c, h) if i["platform"] in ("instagram", "x")}
        assert not any(n in ("acme bakery", "acme hotels", "acme weather") for n in names)
        cleared = c.get("/api/detections/false-positives", headers=h).json()["items"]
        assert len(cleared) >= 3 and all(i["source"] == "sample" and i["score"] < 25 for i in cleared)
        assert all(i["logo_match_percent"] is not None and i["logo_match_percent"] < SIMILAR_MIN for i in cleared if i["category"] == "social")
    finally:
        ctx.__exit__(None, None, None)


def test_sample_scan_without_a_logo_stays_honestly_unavailable_and_caps_below_high(settings, checker):
    ctx, c, h, _b, scan = _sample_scan(settings, checker, with_logo=False)
    try:
        assert scan["counts"]["logo_compared"] == 0 and scan["counts"]["logo_unavailable"] > 0
        items = _social_and_apps(c, h)
        assert items and all(i["score"] < 85 for i in items)
        d = _details(c, h, items[:1])[0]
        assert d["logo"]["status"] == "unavailable" and "Logo unavailable" in d["logo"]["reason"]
    finally:
        ctx.__exit__(None, None, None)


def test_rescan_is_stable(settings, checker):
    ctx, c, h, _b, _scan = _sample_scan(settings, checker)
    try:
        first = {(i["platform"], i["handle"], i["package_id"]): i["score"] for i in _social_and_apps(c, h)}
        run_scan(c, h)
        second = {(i["platform"], i["handle"], i["package_id"]): i["score"] for i in _social_and_apps(c, h)}
        assert first == second
    finally:
        ctx.__exit__(None, None, None)


def test_inline_bytes_are_ignored_for_non_sample_sources(settings, checker):
    """A live collector can never smuggle image bytes in: the copied logo earns nothing and no comparison is recorded."""
    cand = social("instagram", "acmebank.support", "Acme Bank Support", bio="Official support. Share your KYC details in DM.")
    cand.inline_image = LOGO  # byte-identical to the official logo
    assert cand.source == "static" and cand.data_source == "live"
    with make_client(settings, checker, [StaticCollector([cand])]) as c:
        h = headers_for(c, *ADMIN)
        set_mode(c, h, "live")
        b = setup_brand(c, h, accounts=(), apps=())
        assert c.post(f"/api/brands/{b['id']}/logo", files={"file": ("logo.png", LOGO, "image/png")}, headers=h).status_code == 200
        scan = run_scan(c, h)
        assert scan["counts"]["logo_compared"] == 0 and scan["counts"]["logo_unavailable"] == 1
        d = _details(c, h, _social_and_apps(c, h))[0]
        assert d["logo"]["status"] == "unavailable" and d["logo"].get("origin") is None
        assert not any(r["signal"] == "logo_similarity" for r in d["reasons"])
        assert d["source"] == "live"


def test_reset_sample_removes_only_sample_rows_and_a_rescan_restores_them(settings, checker):
    from sqlalchemy import func, select

    from app.models import Detection
    from app.services.sample_reset import reset_sample_detections

    ctx, c, h, b, _scan = _sample_scan(settings, checker)
    try:
        sf = c.app_ref.app.state.session_factory if hasattr(c.app_ref, "app") else c.app_ref.state.session_factory
        before = len(_social_and_apps(c, h))
        with sf() as db:
            live = Detection(brand_id=b["id"], source="live", type="fake_profile", category="social", platform="x", name="Live Row", handle="liverow", score=50, band="low")
            db.add(live)
            db.commit()
            removed = reset_sample_detections(db, b["id"])
            assert removed >= before
            assert db.scalar(select(func.count()).select_from(Detection).where(Detection.source == "sample")) == 0
            assert db.scalar(select(func.count()).select_from(Detection).where(Detection.source == "live")) == 1
            assert reset_sample_detections(db, b["id"]) == 0
        run_scan(c, h)
        assert len([i for i in _social_and_apps(c, h) if i["source"] == "sample"]) == before
    finally:
        ctx.__exit__(None, None, None)

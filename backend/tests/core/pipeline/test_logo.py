import io
import struct

import pytest
from PIL import Image


def img_bytes(fmt="PNG", size=(512, 512), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def upload(client, h, brand, data: bytes, filename="logo.png", ctype="image/png"):
    return client.post(f"/api/brands/{brand['id']}/logo", files={"file": (filename, data, ctype)}, headers=h)


@pytest.mark.parametrize(
    "fmt,ext", [("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")]
)
def test_valid_logo_formats(client, admin_h, brand, fmt, ext, settings):
    r = upload(client, admin_h, brand, img_bytes(fmt))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == ext
    assert (body["width"], body["height"]) == (512, 512)
    assert body["square"] and body["meets_requirements"]
    assert body["url"] == f"/api/brands/{brand['id']}/logo"
    assert len(body["sha256"]) == 64
    got = client.get(body["url"], headers=admin_h)
    assert got.status_code == 200
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-type"] == {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[ext]
    full = client.get(f"/api/brands/{brand['id']}", headers=admin_h).json()
    assert full["logo"]["sha256"] == body["sha256"]
    assert full["profile_health"]["checks"][2]["ok"] is True


def test_type_is_decided_by_bytes_not_filename_or_content_type(client, admin_h, brand):
    # Real PNG with a misleading name and MIME type is accepted...
    assert upload(client, admin_h, brand, img_bytes("PNG"), "evil.exe", "application/octet-stream").status_code == 200
    # ...and non-image bytes with a .png name are refused.
    r = upload(client, admin_h, brand, b"<?php echo 1; ?>" * 50, "logo.png", "image/png")
    assert r.status_code == 415
    assert r.json() == {"detail": "Logo must be a PNG, JPEG or WebP image"}


def test_svg_and_gif_rejected(client, admin_h, brand):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><script>alert(1)</script></svg>'
    assert upload(client, admin_h, brand, svg, "logo.svg", "image/svg+xml").status_code == 415
    assert upload(client, admin_h, brand, img_bytes("GIF"), "logo.gif", "image/gif").status_code == 415


def test_truncated_image_rejected(client, admin_h, brand):
    data = img_bytes("PNG")
    assert upload(client, admin_h, brand, data[: len(data) // 3]).status_code == 415


def test_empty_file_rejected(client, admin_h, brand):
    assert upload(client, admin_h, brand, b"").status_code == 415


def test_too_large_rejected_413(client, admin_h, brand):
    big = img_bytes("PNG")[:100] + b"\0" * (2 * 1024 * 1024 + 1)
    r = upload(client, admin_h, brand, big)
    assert r.status_code == 413
    assert r.json() == {"detail": "Logo is larger than 2 MB"}


def test_exactly_two_mb_is_not_413(client, admin_h, brand):
    # A valid PNG padded to exactly 2 MiB can't be re-created trivially; assert boundary on the service.
    from app.core.pipeline.logo import LogoError, process_logo

    ok = img_bytes("PNG")
    with pytest.raises(LogoError) as e:
        process_logo(ok + b"\0" * (2 * 1024 * 1024 - len(ok) + 1), 2 * 1024 * 1024)
    assert e.value.status_code == 413
    process_logo(ok, len(ok))  # exactly at the limit passes


@pytest.mark.parametrize("size", [(255, 512), (512, 255), (255, 255), (100, 100)])
def test_too_small_rejected(client, admin_h, brand, size):
    r = upload(client, admin_h, brand, img_bytes("PNG", size))
    assert r.status_code == 422
    assert "256" in r.json()["detail"]


@pytest.mark.parametrize("size", [(256, 256), (400, 320), (320, 400), (512, 410), (410, 512)])
def test_square_enough_accepted(client, admin_h, brand, size):
    assert upload(client, admin_h, brand, img_bytes("PNG", size)).status_code == 200


@pytest.mark.parametrize("size", [(1000, 500), (500, 1000), (512, 400), (400, 512), (1000, 256)])
def test_not_square_rejected(client, admin_h, brand, size):
    r = upload(client, admin_h, brand, img_bytes("PNG", size))
    assert r.status_code == 422, size
    assert r.json() == {"detail": "Logo must be roughly square"}


def test_decompression_bomb_rejected(client, admin_h, brand):
    # PNG header declaring 60000x60000 pixels (3.6 Gpx) with a tiny body.
    buf = io.BytesIO()
    Image.new("1", (10, 10)).save(buf, format="PNG")
    data = bytearray(buf.getvalue())
    data[16:24] = struct.pack(">II", 60000, 60000)
    r = upload(client, admin_h, brand, bytes(data))
    assert r.status_code in (415, 422)


def test_stored_under_data_dir_with_hash_name_never_user_filename(client, admin_h, brand, settings):
    upload(client, admin_h, brand, img_bytes("PNG"), "../../etc/passwd.png")
    files = [p for p in settings.data_dir.rglob("*") if p.is_file()]
    assert len(files) == 1
    assert "passwd" not in str(files[0])
    assert settings.data_dir.resolve() in files[0].resolve().parents
    assert files[0].parent.name == f"brand_{brand['id']}"


def test_replacing_logo_removes_old_file(client, admin_h, brand, settings):
    upload(client, admin_h, brand, img_bytes("PNG", color=(1, 2, 3)))
    upload(client, admin_h, brand, img_bytes("PNG", color=(9, 9, 9)))
    assert len([p for p in settings.data_dir.rglob("*") if p.is_file()]) == 1


def test_reencoding_strips_trailing_payload(client, admin_h, brand, settings):
    upload(client, admin_h, brand, img_bytes("PNG") + b"HIDDEN-PAYLOAD")
    stored = next(p for p in settings.data_dir.rglob("*") if p.is_file())
    assert b"HIDDEN-PAYLOAD" not in stored.read_bytes()


def test_get_logo_without_upload_404(client, admin_h, brand):
    assert client.get(f"/api/brands/{brand['id']}/logo", headers=admin_h).status_code == 404


def test_logo_auth_and_role(client, brand, viewer_h):
    data = img_bytes("PNG")
    assert client.post(f"/api/brands/{brand['id']}/logo", files={"file": ("a.png", data)}).status_code == 401
    assert upload(client, viewer_h, brand, data).status_code == 403
    assert client.get(f"/api/brands/{brand['id']}/logo").status_code == 401


def test_logo_missing_file_field_422(client, admin_h, brand):
    r = client.post(f"/api/brands/{brand['id']}/logo", headers=admin_h)
    assert r.status_code == 422

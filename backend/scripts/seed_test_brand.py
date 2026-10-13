"""Create a FICTIONAL test brand ("Acme Bank") through the public API, switch to sample data and run one scan.

Goes through the real endpoints, so every validation rule applies. Safe to re-run: an existing brand is reused.
Nothing here is real: "Acme Bank" and its handles are made up, and the detections it produces are labelled sample data.

Usage (API must be running):  python -m scripts.seed_test_brand [--base-url http://127.0.0.1:8000] [--reset-sample]
Re-running refreshes the brand's sample detections (same handles are updated in place, human decisions are kept). When the sample
generator itself changed (new handles / scores), pass --reset-sample: it deletes ONLY this brand's source="sample" detections
and cleared rows from the database in DATABASE_URL (live data is untouched), then scans again.
Credentials come from ADMIN_EMAIL / ADMIN_PASSWORD in the environment or backend/.env (never printed).
"""
import argparse
import io
import os
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

SERVER_DIR = Path(__file__).resolve().parents[1]

BRAND = {
    "name": "Acme Bank",
    "industry": "Banking",
    "aliases": ["Acme", "AcmeBank", "Acme Bank India"],
    "keywords": ["acme bank", "acme netbanking", "acme rewards"],
    "domains": ["acmebank.example", "netbanking.acmebank.example"],
}
ACCOUNTS = [("instagram", "acmebank"), ("x", "acmebank"), ("facebook", "acmebank.official"), ("youtube", "acmebank"), ("linkedin", "acmebank")]
APP = {"name": "Acme Bank Mobile", "package_id": "com.acmebank.mobile", "publisher": "Acme Bank Ltd", "stores": ["google_play", "app_store"]}


def make_logo() -> bytes:
    img = Image.new("RGB", (512, 512), (20, 40, 110))
    d = ImageDraw.Draw(img)
    d.ellipse((96, 96, 416, 416), fill=(245, 190, 40))
    d.rectangle((176, 200, 336, 320), fill=(20, 40, 110))
    d.polygon([(256, 150), (170, 200), (342, 200)], fill=(20, 40, 110))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def env_value(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env = SERVER_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--reset-sample", action="store_true", help="delete this brand's sample-data detections first (sample rows only)")
    args = ap.parse_args()
    email, password = env_value("ADMIN_EMAIL"), env_value("ADMIN_PASSWORD")
    if not (email and password):
        print("ADMIN_EMAIL / ADMIN_PASSWORD not set (environment or backend/.env).")
        return 2
    c = httpx.Client(base_url=args.base_url, timeout=60)
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        print(f"Login failed ({r.status_code}). Is the API running and are the admin credentials right?")
        return 1
    h = {"Authorization": "Bearer " + r.json()["access_token"]}

    brands = c.get("/api/brands", headers=h).json()
    brands = brands if isinstance(brands, list) else brands.get("items", [])
    if brands:
        brand = brands[0]
        print(f"Brand already exists: {brand['name']} (id {brand['id']}); reusing it.")
    else:
        r = c.post("/api/brands", json=BRAND, headers=h)
        r.raise_for_status()
        brand = r.json()
        print(f"Created brand {brand['name']} (id {brand['id']}).")
        r = c.post(f"/api/brands/{brand['id']}/logo", files={"file": ("logo.png", make_logo(), "image/png")}, headers=h)
        print("Logo:", r.status_code, "" if r.status_code == 200 else r.text[:150])
        for platform, handle in ACCOUNTS:
            r = c.post(f"/api/brands/{brand['id']}/accounts", json={"platform": platform, "handle": handle}, headers=h)
            print(f"Account {platform}/{handle}:", r.status_code, "" if r.status_code == 201 else r.text[:150])
        r = c.post(f"/api/brands/{brand['id']}/apps", json=APP, headers=h)
        print("App:", r.status_code, "" if r.status_code == 201 else r.text[:150])

    if args.reset_sample:
        from app.config.config import get_settings
        from app.db.session import make_engine, make_session_factory
        from app.services.sample_reset import reset_sample_detections

        with make_session_factory(make_engine(get_settings().database_url))() as db:
            print(f"Removed {reset_sample_detections(db, brand['id'])} sample detections (live data untouched).")

    r = c.put("/api/settings/data-mode", json={"data_mode": "sample"}, headers=h)
    print("Data mode -> sample:", r.status_code)

    r = c.post("/api/scan", json={"scope": "all", "brand_id": brand["id"]}, headers=h)
    print("Scan start:", r.status_code)
    job = r.json().get("job_id")
    for _ in range(60):
        s = c.get(f"/api/jobs/{job}", headers=h).json()
        if s.get("status") in ("completed", "partial", "failed") or s.get("state") in ("done", "error"):
            break
        time.sleep(1)
    print("Scan result:", {k: s.get(k) for k in ("status", "state", "counts") if k in s})
    d = c.get("/api/dashboard", headers=h)
    print("Dashboard:", d.status_code, "is_sample =", d.json().get("is_sample") if d.status_code == 200 else d.text[:100])
    return 0


if __name__ == "__main__":
    sys.exit(main())

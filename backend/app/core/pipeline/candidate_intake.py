"""Manual entry and CSV / JSON import of candidates.

Rows are validated strictly (unknown fields refused, lengths capped), text is cleaned (control characters removed, spreadsheet-formula
prefixes neutralised, never rendered as HTML by the API), and each valid row goes through the SAME pipeline as a scan row: allowlist first
(official handles, package ids, official domains, VERIFIED publisher ids), then scoring, then the de-duplicating upsert. Provenance is
`manual` or `import` on the detection (`origin`). No URL in a row is ever opened: reachability and logo downloads are off for these rows;
only an image uploaded through /api/candidate-images can be compared.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.core.collectors.base import Candidate, candidate_from_web_url
from app.core.pipeline import scan_pipeline as sp
from app.models import Brand, User
from app.services import audit
from app.services import validators as v

Kind = Literal["social", "app", "web"]
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_FORMULA = ("=", "+", "-", "@", "\t", "\r")

SOCIAL_COLUMNS = ("platform", "handle", "display_name", "url", "bio", "followers", "following", "posts", "created_at", "verified", "image_id")
APP_COLUMNS = ("store", "package_id", "name", "publisher", "publisher_id", "url", "description", "installs", "rating_count", "image_id")
WEB_COLUMNS = ("url", "title", "note")
COLUMNS = {"social": SOCIAL_COLUMNS, "app": APP_COLUMNS, "web": WEB_COLUMNS}


def clean_text(s: str | None, limit: int) -> str | None:
    """Plain text: no control characters, a leading formula character is defused, bounded. Empty -> None."""
    if s is None:
        return None
    t = " ".join(_CTRL.sub(" ", str(s)).split())
    if t.startswith(_FORMULA):
        t = "'" + t
    return t[:limit] or None


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SocialIn(_Row):
    platform: str
    handle: str = Field(min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    bio: str | None = Field(default=None, max_length=2000)
    followers: int | None = Field(default=None, ge=0, le=10**10)
    following: int | None = Field(default=None, ge=0, le=10**10)
    posts: int | None = Field(default=None, ge=0, le=10**9)
    created_at: date | None = None
    verified: bool | None = None
    image_id: int | None = Field(default=None, ge=1)

    @field_validator("platform")
    @classmethod
    def _p(cls, p: str) -> str:
        p = p.strip().lower()
        if p == "twitter":
            p = "x"
        if p not in (*v.ACCOUNT_PLATFORMS, "telegram"):
            raise ValueError(f"platform must be one of: {', '.join((*v.ACCOUNT_PLATFORMS, 'telegram'))}")
        return p


class AppIn(_Row):
    store: str
    package_id: str = Field(min_length=1, max_length=155)
    name: str = Field(min_length=1, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    publisher_id: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=4000)
    installs: int | None = Field(default=None, ge=0, le=10**12)
    rating_count: int | None = Field(default=None, ge=0, le=10**10)
    image_id: int | None = Field(default=None, ge=1)

    @field_validator("store")
    @classmethod
    def _s(cls, s: str) -> str:
        s = s.strip().lower()
        if s not in (*v.APP_STORES, "third_party_apk"):
            raise ValueError("store must be one of: google_play, app_store, third_party_apk")
        return s


class WebIn(_Row):
    url: str = Field(min_length=1, max_length=500)
    title: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=1000)


MODELS: dict[str, type[_Row]] = {"social": SocialIn, "app": AppIn, "web": WebIn}


def _http_url(raw: str | None) -> str | None:
    if not raw:
        return None
    u = raw.strip()
    if "://" not in u:
        u = "https://" + u
    if not re.match(r"^https?://[^\s/?#]+", u, re.I) or len(u) > 500:
        raise ValueError("url must be an http(s) address")
    return u


def to_candidate(kind: str, row: _Row, origin: str) -> Candidate:
    """Validated row -> Candidate (source = manual | import). Raises ValueError with a plain message."""
    if kind == "social":
        r: SocialIn = row  # type: ignore[assignment]
        handle = v.normalize_handle(r.handle, r.platform if r.platform != "telegram" else None)
        url = _http_url(r.url) or (f"https://{v.build_account_url(r.platform, handle)}" if r.platform in v.URL_PREFIX else f"https://t.me/{handle}")
        return Candidate(platform=r.platform, kind="social", handle=handle, display_name=clean_text(r.display_name, 200) or handle, profile_url=url,
                         source=origin, bio=clean_text(r.bio, 2000), followers=r.followers, following=r.following, posts=r.posts,
                         created_at=r.created_at, verified=r.verified, extras={"image_id": r.image_id} if r.image_id else {})
    if kind == "app":
        a: AppIn = row  # type: ignore[assignment]
        store = {"google_play": "google_play", "app_store": "apple_app_store", "third_party_apk": "apkpure"}[a.store]
        pkg = a.package_id.strip()
        if a.store != "third_party_apk":
            pkg = v.validate_package_id(pkg, [a.store])
        extras: dict[str, Any] = {}
        if a.image_id:
            extras["image_id"] = a.image_id
        if a.publisher_id:
            extras["publisher_id"] = clean_text(a.publisher_id, 120)
        if a.installs is not None:
            extras["installs_count"] = a.installs
        return Candidate(platform=a.store, kind="app", handle=pkg, display_name=clean_text(a.name, 200), profile_url=_http_url(a.url), source=origin,
                         bio=clean_text(a.description, 4000), store=store, publisher=clean_text(a.publisher, 200), rating_count=a.rating_count, extras=extras)
    w: WebIn = row  # type: ignore[assignment]
    c = candidate_from_web_url(_http_url(w.url), source=origin, title=clean_text(w.title, 300), snippet=clean_text(w.note, 1000))
    if c is None:
        raise ValueError("url is not a usable website address (IP addresses are not compared)")
    return c


# ---------------------------------------------------------------------------------------------------------
# parsing uploaded files


class ImportFileError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def decode_bytes(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        enc = "utf-16"
    else:
        enc = "utf-8-sig"
    try:
        return data.decode(enc)
    except UnicodeDecodeError as exc:
        raise ImportFileError(422, "The file must be UTF-8 (or UTF-16 with a byte-order mark) text") from exc


def parse_rows(data: bytes, fmt: str, kind: str, max_rows: int) -> list[dict[str, Any]]:
    text = decode_bytes(data)
    cols = COLUMNS[kind]
    if fmt == "csv":
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        try:
            header = [h.strip() for h in (reader.fieldnames or [])]
            if not header:
                raise ImportFileError(422, "The CSV file has no header row")
            unknown = [h for h in header if h not in cols]
            if unknown:
                raise ImportFileError(422, f"Unknown column(s): {', '.join(unknown)}. Allowed: {', '.join(cols)}")
            reader.fieldnames = header
            rows: list[dict[str, Any]] = []
            for r in reader:
                if len(rows) >= max_rows:
                    raise ImportFileError(413, f"The file has more than {max_rows} rows")
                if None in r:
                    rows.append({"__error__": "Too many values in this row"})
                    continue
                rows.append({k: (val.strip() if val is not None else "") for k, val in r.items()})
        except csv.Error as exc:
            raise ImportFileError(422, f"The CSV file is malformed: {exc}") from exc
        return rows
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise ImportFileError(422, "The JSON file is not valid JSON") from exc
    if isinstance(doc, dict) and isinstance(doc.get("rows"), list):
        doc = doc["rows"]
    if not isinstance(doc, list):
        raise ImportFileError(422, 'The JSON file must be an array of objects (or {"rows": [...]})')
    if len(doc) > max_rows:
        raise ImportFileError(413, f"The file has more than {max_rows} rows")
    return [r if isinstance(r, dict) else {"__error__": "Row is not an object"} for r in doc]


def _blank_to_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (None if isinstance(val, str) and val == "" else val) for k, val in d.items()}


def validate_row(kind: str, raw: dict[str, Any], origin: str) -> Candidate:
    if "__error__" in raw:
        raise ValueError(raw["__error__"])
    try:
        row = MODELS[kind].model_validate(_blank_to_none(raw))
    except ValidationError as exc:
        raise ValueError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())[:300]) from exc
    return to_candidate(kind, row, origin)


# ---------------------------------------------------------------------------------------------------------
# running rows through the pipeline


def run_rows(ctx: sp.ScanContext, db: Session, brand: Brand, user: User, kind: str, raws: list[dict[str, Any]], origin: str, *, dry_run: bool) -> dict:
    results: list[dict[str, Any]] = []
    cands: list[tuple[int, Candidate]] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(raws, start=1):
        try:
            c = validate_row(kind, raw, origin)
        except ValueError as exc:
            results.append({"row": i, "status": "error", "message": str(exc)[:300]})
            continue
        key = sp.canonical_key(c)
        if key in seen:
            results.append({"row": i, "status": "duplicate", "message": f"Same candidate as row {seen[key]} in this file."})
            continue
        seen[key] = i
        cands.append((i, c))
    if dry_run:
        results += [{"row": i, "status": "valid", "message": "Would be analysed (dry run: nothing was saved)."} for i, _ in cands]
    else:
        counts: dict[str, int] = defaultdict(int)
        env = sp._Env(ctx, db, brand)
        env.allow.learn_apple_ids([c for _, c in cands])
        sp._reputation_pass(ctx, env, [c for _, c in cands], counts)
        existing = {d.canonical_key: d for d in db.query(sp.Detection).filter(sp.Detection.brand_id == brand.id)}
        seen_official: dict = {}
        for i, c in cands:
            now = ctx.now()
            hit = env.allow.check(c)
            if hit is not None:
                sp._record_official(db, None, brand, c, hit, now, seen_official)
                results.append({"row": i, "status": "official", "message": f"Excluded: {hit.rule}"})
                continue
            oc = sp.evaluate(ctx, env, brand, c, counts)
            if oc.kind == "ignored" or oc.score is None:
                results.append({"row": i, "status": "not_flagged", "message": "Evaluated; nothing ties it to the brand, so it was not saved."})
                continue
            before = existing.get(sp.canonical_key(c))
            sp.upsert_detection(ctx, db, None, brand, oc, existing, counts)
            det = existing.get(sp.canonical_key(c))
            status = ("updated" if before is not None else "created") if oc.kind == "hit" else "cleared"
            results.append({"row": i, "status": status, "detection_id": det.id if det else None, "score": oc.score.score,
                            "severity": oc.assessment.severity if oc.assessment else None,
                            "message": "Below the review threshold: kept in the cleared list." if oc.kind == "cleared" else ""})
        audit.record(db, user, "candidates.imported" if origin == "import" else "candidates.added", f"{kind} ({origin})",
                     f"{len(cands)} analysed, {sum(r['status'] in ('created', 'updated') for r in results)} detections")
        db.commit()
    results.sort(key=lambda r: r["row"])
    errors = sum(r["status"] == "error" for r in results)
    return {"dry_run": dry_run, "kind": kind, "origin": origin, "total": len(raws), "rejected": errors,
            "duplicates": sum(r["status"] == "duplicate" for r in results), "accepted": len(raws) - errors - sum(r["status"] == "duplicate" for r in results),
            "created": sum(r["status"] == "created" for r in results), "updated": sum(r["status"] == "updated" for r in results),
            "official": sum(r["status"] == "official" for r in results), "results": results}

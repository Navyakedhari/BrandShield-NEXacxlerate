"""Evidence snapshots: the candidate data saved under DATA_DIR, content-addressed, with its SHA-256.

The stored bytes ARE what was hashed, so anyone can re-hash the file and compare with `evidence.sha256`.
Volatile fields (fetch time) are left out of the snapshot so an unchanged listing keeps the same hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.collectors.base import Candidate

_VOLATILE = {"fetched_at", "cached_at", "stale"}


def snapshot_payload(c: Candidate) -> dict[str, Any]:
    d = c.to_dict()
    return {k: v for k, v in d.items() if k not in _VOLATILE}


def snapshot_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def save_snapshot(data_dir: Path, payload: dict[str, Any]) -> tuple[str, str]:
    """Write the snapshot (once per distinct content). Returns (sha256 hex, path relative to data_dir)."""
    raw = snapshot_bytes(payload)
    sha = hashlib.sha256(raw).hexdigest()
    rel = Path("evidence") / sha[:2] / f"{sha}.json"
    dest = data_dir / rel
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    return sha, rel.as_posix()


def save_screenshot(data_dir: Path, png: bytes) -> tuple[str, str]:
    sha = hashlib.sha256(png).hexdigest()
    rel = Path("evidence") / "screenshots" / sha[:2] / f"{sha}.png"
    dest = data_dir / rel
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(png)
    return sha, rel.as_posix()


def safe_path(data_dir: Path, rel: str | None) -> Path | None:
    """Resolve a stored relative path, refusing anything outside DATA_DIR."""
    if not rel:
        return None
    root = Path(data_dir).resolve()
    path = (root / rel).resolve()
    return path if root in path.parents and path.is_file() else None


def read_snapshot(data_dir: Path, rel: str | None, expected_sha: str | None) -> tuple[dict | None, bool]:
    """(snapshot json, hash_matches). Missing/unreadable file -> (None, False)."""
    path = safe_path(data_dir, rel)
    if path is None:
        return None, False
    raw = path.read_bytes()
    ok = bool(expected_sha) and hashlib.sha256(raw).hexdigest() == expected_sha
    try:
        return json.loads(raw.decode("utf-8")), ok
    except (ValueError, UnicodeDecodeError):
        return None, False

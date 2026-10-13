"""Cache for hosted-model answers: in-memory LRU + a persistent table (survives restarts, shared by api and worker).

Key = SHA-256(provider model + prompt version + kind + brand-context hash + sanitized candidate text). The brand context is part of the
key because the answer depends on it. Only successful, validated answers are cached (never errors)."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.config.time import as_utc, utcnow

CACHE_TTL = timedelta(days=30)


def cache_key(model: str, prompt_version: str, kind: str, context: str, text: str) -> str:
    h = hashlib.sha256()
    for part in (model, prompt_version, kind, hashlib.sha256(context.encode()).hexdigest(), text):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class AnswerCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def put(self, key: str, provider: str, value: dict[str, Any]) -> None: ...


class MemoryCache:
    def __init__(self, max_size: int = 512) -> None:
        self.max_size = max_size
        self._d: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

    def put(self, key: str, provider: str, value: dict[str, Any]) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.max_size:
            self._d.popitem(last=False)


class DbCache:
    """Persistent layer (table text_analysis_cache) behind a memory layer. A database problem never fails an analysis."""

    def __init__(self, session_factory: sessionmaker[Session], memory: MemoryCache | None = None) -> None:
        self.sf = session_factory
        self.memory = memory or MemoryCache()

    def get(self, key: str) -> dict[str, Any] | None:
        v = self.memory.get(key)
        if v is not None:
            return v
        from app.models import TextAnalysisCache

        try:
            with self.sf() as db:
                row = db.get(TextAnalysisCache, key)
                if row is None or utcnow() - as_utc(row.created_at) > CACHE_TTL:
                    return None
                value = json.loads(json.dumps(row.value))
        except Exception:  # noqa: BLE001 - cache is best-effort
            return None
        self.memory.put(key, "", value)
        return value

    def put(self, key: str, provider: str, value: dict[str, Any]) -> None:
        self.memory.put(key, provider, value)
        from app.models import TextAnalysisCache

        try:
            with self.sf() as db:
                row = db.get(TextAnalysisCache, key)
                if row is None:
                    db.add(TextAnalysisCache(key=key, provider=provider, value=value, created_at=utcnow()))
                else:
                    row.value, row.created_at = value, utcnow()
                db.commit()
        except Exception:  # noqa: BLE001
            pass

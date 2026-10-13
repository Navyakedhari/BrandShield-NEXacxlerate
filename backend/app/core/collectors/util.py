"""Small injectable helpers: spacing rate limiter and TTL cache (both take a clock so tests can fake time)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

Clock = Callable[[], float]
Sleep = Callable[[float], None]


class SpacingLimiter:
    """Enforces a minimum gap between calls (calls_per_minute=20 -> one call every 3 seconds)."""

    def __init__(self, calls_per_minute: int, *, clock: Clock = time.monotonic, sleep: Sleep = time.sleep) -> None:
        self.interval = 60.0 / max(1, calls_per_minute)
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> float:
        """Block until the next call is allowed. Returns the seconds waited."""
        waited = 0.0
        now = self._clock()
        if self._last is not None:
            gap = now - self._last
            if gap < self.interval:
                waited = self.interval - gap
                self._sleep(waited)
        self._last = self._clock()
        return waited


class TTLCache:
    def __init__(self, ttl_seconds: float, *, clock: Clock = time.monotonic, max_items: int = 512) -> None:
        self.ttl = ttl_seconds
        self._clock = clock
        self.max_items = max_items
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        stored, value = hit
        if self._clock() - stored >= self.ttl:
            del self._data[key]
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        if len(self._data) >= self.max_items:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest]
        self._data[key] = (self._clock(), value)

    def __len__(self) -> int:
        return len(self._data)

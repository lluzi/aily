from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class FixedWindowRateLimiter:
    max_requests: int
    window_seconds: float
    _buckets: dict[str, tuple[float, int]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def allow(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        safe_key = key or "anonymous"
        with self._lock:
            window_start, count = self._buckets.get(safe_key, (now, 0))
            if now - window_start >= self.window_seconds:
                self._buckets[safe_key] = (now, 1)
                return True, self.window_seconds
            if count >= self.max_requests:
                retry_after = max(0.0, self.window_seconds - (now - window_start))
                return False, retry_after
            self._buckets[safe_key] = (window_start, count + 1)
            return True, max(0.0, self.window_seconds - (now - window_start))

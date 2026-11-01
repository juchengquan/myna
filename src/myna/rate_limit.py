"""In-memory token-bucket rate limiter for the MCP endpoint.

One bucket per key (caller label for authenticated requests, client IP
otherwise). Tokens refill continuously at `rate_per_minute / 60.0` per
second, capped at `capacity`. A request consumes one token; if the
bucket is empty, the request is denied with a `retry_after` hint in
seconds.

Limitations:
- In-memory only — buckets are not shared across uvicorn workers or
  replicas. For multi-replica enforcement, swap in a Redis-backed
  store behind the same interface.

Idle buckets are swept once a minute. A bucket idle for that long has
fully refilled, so dropping it is the same as keeping it: the next
request from that key starts with a full bucket either way.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

# Refill rate is capacity / 60, so an empty bucket always refills in 60s.
_FULL_REFILL_SECONDS = 60.0


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class RateLimiter:
    def __init__(self, rate_per_minute: int):
        self.rate_per_minute = rate_per_minute
        self.capacity = float(max(rate_per_minute, 0))
        self.refill_per_second = self.capacity / 60.0
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()
        self._clock = time.monotonic
        self._last_sweep = 0.0

    @property
    def enabled(self) -> bool:
        return self.rate_per_minute > 0

    async def check(self, key: str) -> tuple[bool, float]:
        """Try to consume one token for `key`. Returns (allowed, retry_after_seconds)."""
        if not self.enabled:
            return True, 0.0

        now = self._clock()
        async with self._lock:
            self._sweep_idle(now)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.capacity, last_refill=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_second)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0

            deficit = 1.0 - bucket.tokens
            retry_after = deficit / self.refill_per_second if self.refill_per_second else 0.0
            return False, retry_after

    def _sweep_idle(self, now: float) -> None:
        if now - self._last_sweep < _FULL_REFILL_SECONDS:
            return
        self._last_sweep = now
        self._buckets = {
            key: bucket
            for key, bucket in self._buckets.items()
            if now - bucket.last_refill < _FULL_REFILL_SECONDS
        }

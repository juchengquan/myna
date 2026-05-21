"""In-memory TTL cache for tool results.

`@cached(ttl_seconds=...)` wraps an MCP tool function so identical
calls within the TTL window return the cached value instead of
re-running the body. Use it for **idempotent** tools — `get_weather`,
external HTTP fetches, expensive pure computations. Do **not** use it
for tools that take a `Context` parameter (sampling, elicitation,
streaming), tools with intentional side effects, or anything where
each call is meant to do work.

Each cached tool gets its own `TTLCache` instance — keys are scoped
per-tool, not global. Cache hits/misses are recorded in the
Prometheus `myna_tool_cache_total{tool, outcome}` counter and as an
`mcp.cache.outcome` attribute on the active tool-call span.

Limitations:
- In-process only; not shared across uvicorn workers or replicas.
  For shared caching, swap the in-memory dict for Redis via the same
  decorator interface.
- No size cap. Fine for small key spaces (e.g. a finite set of
  locations); add an LRU bound if user-controllable inputs explode
  the keyspace.
- Caches the *return value*, not intermediate state — for tools that
  raise, the exception is not cached.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from opentelemetry import trace

F = TypeVar("F", bound=Callable[..., Any])


class TTLCache:
    """Async-safe TTL cache keyed by a string. One instance per cached tool."""

    def __init__(self, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._clock = time.monotonic

    async def lookup(self, key: str) -> tuple[bool, Any]:
        """Return `(hit, value)`. On miss, `value` is `None`."""
        now = self._clock()
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False, None
            expires_at, value = entry
            if expires_at <= now:
                del self._store[key]
                return False, None
            return True, value

    async def store(self, key: str, value: Any) -> None:
        async with self._lock:
            self._store[key] = (self._clock() + self.ttl, value)

    async def clear(self) -> None:
        """Drop all entries. Mostly a test hook."""
        async with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


def cached(
    ttl_seconds: float,
    *,
    label: str | None = None,
) -> Callable[[F], F]:
    """Decorator: cache an MCP tool's return value for `ttl_seconds`.

    Order matters when stacked with `@mcp.tool()` — `@cached` goes on
    the inside, `@mcp.tool()` on the outside, so FastMCP still sees the
    original function's signature (`functools.wraps` carries it
    through via `__wrapped__`).

    The Prometheus counter `myna_tool_cache_total` labels each entry
    with `tool=<name>`. By default `<name>` is the wrapped function's
    `__name__`; pass `label="…"` to override (useful when caching at a
    helper layer rather than on the tool itself).
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    def decorator(fn: F) -> F:
        from myna.observability import TOOL_CACHE  # avoid circular import

        cache = TTLCache(ttl_seconds)
        is_async = inspect.iscoroutinefunction(fn)
        tool_name = label or fn.__name__

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _make_key(args, kwargs)
            hit, value = await cache.lookup(key)

            span = trace.get_current_span()
            if hit:
                TOOL_CACHE.labels(tool=tool_name, outcome="hit").inc()
                span.set_attribute("mcp.cache.outcome", "hit")
                return value

            TOOL_CACHE.labels(tool=tool_name, outcome="miss").inc()
            span.set_attribute("mcp.cache.outcome", "miss")
            result = await fn(*args, **kwargs) if is_async else fn(*args, **kwargs)
            await cache.store(key, result)
            return result

        # Expose the cache for tests / inspection.
        wrapper._myna_cache = cache  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def _make_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Canonical, stable cache key derived from positional + keyword args."""
    try:
        canonical = json.dumps([args, kwargs], sort_keys=True, default=str)
    except TypeError:
        canonical = repr((args, kwargs))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

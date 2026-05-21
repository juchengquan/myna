from __future__ import annotations

import pytest

from myna.cache import TTLCache, cached
from myna.observability import TOOL_CACHE


@pytest.mark.asyncio
async def test_ttlcache_hit_then_miss_after_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    c = TTLCache(ttl_seconds=10)

    fake_now = [0.0]
    monkeypatch.setattr(c, "_clock", lambda: fake_now[0])

    await c.store("k", "v")
    hit, value = await c.lookup("k")
    assert hit and value == "v"

    # Inside TTL — still a hit.
    fake_now[0] = 9.99
    hit, value = await c.lookup("k")
    assert hit and value == "v"

    # Past TTL — miss + eviction.
    fake_now[0] = 10.01
    hit, value = await c.lookup("k")
    assert not hit and value is None
    assert len(c) == 0


def test_ttlcache_rejects_non_positive_ttl() -> None:
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=-1)


@pytest.mark.asyncio
async def test_cached_decorator_returns_same_value_on_repeat() -> None:
    calls = {"n": 0}

    @cached(ttl_seconds=60)
    def add(a: int, b: int) -> int:
        calls["n"] += 1
        return a + b

    assert await add(2, 3) == 5
    assert await add(2, 3) == 5
    assert await add(2, 3) == 5
    assert calls["n"] == 1  # body ran once


@pytest.mark.asyncio
async def test_cached_decorator_keys_by_args() -> None:
    @cached(ttl_seconds=60)
    def double(x: int) -> int:
        return x * 2

    assert await double(1) == 2
    assert await double(2) == 4
    assert await double(1) == 2
    assert len(double._myna_cache) == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cached_decorator_works_with_async_function() -> None:
    calls = {"n": 0}

    @cached(ttl_seconds=60)
    async def fetch(x: int) -> int:
        calls["n"] += 1
        return x

    assert await fetch(7) == 7
    assert await fetch(7) == 7
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cached_increments_hit_and_miss_counters() -> None:
    # Use a self-contained cached function so this test stays
    # independent of any specific tool's implementation or upstream.
    @cached(ttl_seconds=60, label="test_counter_tool")
    def square(x: int) -> int:
        return x * x

    before_hit = _counter("test_counter_tool", "hit")
    before_miss = _counter("test_counter_tool", "miss")

    await square(5)  # miss
    await square(5)  # hit
    await square(5)  # hit

    assert _counter("test_counter_tool", "miss") == before_miss + 1
    assert _counter("test_counter_tool", "hit") == before_hit + 2


@pytest.mark.asyncio
async def test_cached_label_overrides_function_name() -> None:
    @cached(ttl_seconds=60, label="custom-label")
    def f(x: int) -> int:
        return x

    before = _counter("custom-label", "miss")
    await f(1)
    assert _counter("custom-label", "miss") == before + 1


def _counter(tool: str, outcome: str) -> float:
    return float(TOOL_CACHE.labels(tool=tool, outcome=outcome)._value.get())  # type: ignore[attr-defined]

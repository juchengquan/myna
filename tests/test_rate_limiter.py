from __future__ import annotations

import pytest

from myna.rate_limit import RateLimiter


@pytest.mark.asyncio
async def test_disabled_when_rate_zero() -> None:
    limiter = RateLimiter(rate_per_minute=0)
    assert not limiter.enabled
    for _ in range(1000):
        allowed, retry = await limiter.check("anyone")
        assert allowed
        assert retry == 0.0


@pytest.mark.asyncio
async def test_consumes_then_denies_when_bucket_empty() -> None:
    limiter = RateLimiter(rate_per_minute=3)

    # Burst of 3 should succeed.
    for _ in range(3):
        allowed, retry = await limiter.check("k")
        assert allowed
        assert retry == 0.0

    # 4th immediate call is denied with a positive retry hint.
    allowed, retry = await limiter.check("k")
    assert not allowed
    assert retry > 0


@pytest.mark.asyncio
async def test_keys_are_isolated() -> None:
    limiter = RateLimiter(rate_per_minute=2)
    assert (await limiter.check("a"))[0]
    assert (await limiter.check("a"))[0]
    assert not (await limiter.check("a"))[0]
    # `b` still has a full bucket.
    assert (await limiter.check("b"))[0]
    assert (await limiter.check("b"))[0]


@pytest.mark.asyncio
async def test_tokens_refill_over_time(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = RateLimiter(rate_per_minute=60)  # 1 token/sec, capacity 60

    fake_now = [0.0]
    monkeypatch.setattr(limiter, "_clock", lambda: fake_now[0])

    # Drain the bucket.
    for _ in range(60):
        assert (await limiter.check("k"))[0]
    assert not (await limiter.check("k"))[0]

    # Advance 2 seconds — expect 2 tokens to be available again.
    fake_now[0] = 2.0
    assert (await limiter.check("k"))[0]
    assert (await limiter.check("k"))[0]
    assert not (await limiter.check("k"))[0]


@pytest.mark.asyncio
async def test_idle_buckets_are_swept(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = RateLimiter(rate_per_minute=60)

    fake_now = [0.0]
    monkeypatch.setattr(limiter, "_clock", lambda: fake_now[0])

    await limiter.check("idle")
    fake_now[0] = 30.0
    await limiter.check("active")

    fake_now[0] = 61.0
    await limiter.check("new")

    assert set(limiter._buckets) == {"active", "new"}


@pytest.mark.asyncio
async def test_sweep_keeps_drained_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = RateLimiter(rate_per_minute=2)

    fake_now = [60.0]
    monkeypatch.setattr(limiter, "_clock", lambda: fake_now[0])

    assert (await limiter.check("k"))[0]
    assert (await limiter.check("k"))[0]
    assert not (await limiter.check("k"))[0]

    # 50s refills 1.67 of 2 tokens. A sweep must not reset the bucket to
    # full, so only one more request gets through.
    fake_now[0] = 110.0
    limiter._last_sweep = 0.0
    assert (await limiter.check("k"))[0]
    assert not (await limiter.check("k"))[0]

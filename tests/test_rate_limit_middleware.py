from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myna.config import get_settings
from myna.main import create_app
from myna.observability import RATE_LIMIT_HITS, metric_value


def _post(client: TestClient, *, token: str | None = None) -> int:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=headers)
    return resp.status_code


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_429_after_quota_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS", raising=False)
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "3")

    with TestClient(create_app()) as client:
        # 3 requests inside the burst.
        for _ in range(3):
            assert _post(client) != 429
        # 4th request should be limited.
        resp = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 429
        assert resp.headers.get("retry-after") is not None
        assert resp.json()["error"] == "rate_limited"


def test_zero_disables_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS", raising=False)
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "0")

    with TestClient(create_app()) as client:
        for _ in range(20):
            assert _post(client) != 429


def test_two_callers_have_independent_quotas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", '{"sk-a":"client-a","sk-b":"client-b"}')
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "2")

    with TestClient(create_app()) as client:
        # client-a exhausts its quota.
        assert _post(client, token="sk-a") != 429
        assert _post(client, token="sk-a") != 429
        assert _post(client, token="sk-a") == 429

        # client-b still has a fresh bucket.
        assert _post(client, token="sk-b") != 429
        assert _post(client, token="sk-b") != 429


def test_rate_limit_hits_counter_increments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS", raising=False)
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "1")

    before = _counter_value("ip")
    with TestClient(create_app()) as client:
        _post(client)
        _post(client)  # this one should be limited
    assert _counter_value("ip") == before + 1


def _counter_value(key_kind: str) -> float:
    return metric_value(RATE_LIMIT_HITS, key_kind=key_kind)

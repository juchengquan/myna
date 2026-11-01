from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myna.config import get_settings
from myna.main import create_app
from myna.observability import RATE_LIMIT_HITS, metric_value


def _mcp_request() -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def _mcp_headers(token: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_dev_allows_unauthenticated_when_no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS", raising=False)

    with TestClient(create_app()) as client:
        resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers())
        assert resp.status_code != 401


def test_production_rejects_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "{}")
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")

    with TestClient(create_app()) as client:
        resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers())
        assert resp.status_code == 401


def test_rejects_unknown_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", '{"sk-good":"client-a"}')
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")

    with TestClient(create_app()) as client:
        resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers("sk-bad"))
        assert resp.status_code == 401


def test_accepts_known_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", '{"sk-good":"client-a"}')
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")

    with TestClient(create_app()) as client:
        resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers("sk-good"))
        assert resp.status_code != 401


def test_any_configured_token_resolves_to_its_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", '{"sk-a":"client-a","sk-b":"client-b"}')
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")

    with TestClient(create_app()) as client:
        for token in ("sk-a", "sk-b"):
            resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers(token))
            assert resp.status_code != 401


def test_repeated_failures_are_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", '{"sk-good":"client-a"}')
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "2")
    before = metric_value(RATE_LIMIT_HITS, key_kind="auth_failure")

    with TestClient(create_app()) as client:
        def status(token: str) -> int:
            headers = _mcp_headers(token)
            return client.post("/mcp/", json=_mcp_request(), headers=headers).status_code

        assert status("sk-bad") == 401
        assert status("sk-bad") == 401
        assert status("sk-bad") == 429
        # A throttled address can still use a valid key.
        assert status("sk-good") not in (401, 429)

    assert metric_value(RATE_LIMIT_HITS, key_kind="auth_failure") == before + 1


def test_failures_not_throttled_when_rate_limit_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", '{"sk-good":"client-a"}')
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "0")

    with TestClient(create_app()) as client:
        for _ in range(5):
            resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers("sk-bad"))
            assert resp.status_code == 401


def test_admin_api_unaffected_by_mcp_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS", raising=False)

    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/admin/tools").status_code == 200

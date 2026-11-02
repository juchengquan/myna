from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myna.config import get_settings
from myna.main import create_app
from myna.observability import RATE_LIMIT_HITS, TOOL_CALLS, metric_value


def _client() -> TestClient:
    # The MCP endpoint only accepts local hosts that include a port.
    return TestClient(create_app(), base_url="http://localhost:8000")


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

    with _client() as client:
        resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers())
        assert resp.status_code == 200


def test_production_rejects_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "")
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")

    with _client() as client:
        resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers())
        assert resp.status_code == 401


def test_rejects_unknown_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "client-a:sk-good")
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")

    with _client() as client:
        resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers("sk-bad"))
        assert resp.status_code == 401


def test_accepts_known_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "client-a:sk-good")
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")

    with _client() as client:
        resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers("sk-good"))
        assert resp.status_code == 200


def test_any_configured_token_resolves_to_its_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "client-a:sk-a,client-b:sk-b")
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")

    with _client() as client:
        for token in ("sk-a", "sk-b"):
            resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers(token))
            assert resp.status_code == 200


def test_repeated_failures_are_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "client-a:sk-good")
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "2")
    before = metric_value(RATE_LIMIT_HITS, key_kind="auth_failure")

    with _client() as client:
        def status(token: str) -> int:
            headers = _mcp_headers(token)
            return client.post("/mcp/", json=_mcp_request(), headers=headers).status_code

        assert status("sk-bad") == 401
        assert status("sk-bad") == 401
        assert status("sk-bad") == 429
        # A throttled address can still use a valid key.
        assert status("sk-good") == 200

    assert metric_value(RATE_LIMIT_HITS, key_kind="auth_failure") == before + 1


def test_failures_not_throttled_when_rate_limit_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "client-a:sk-good")
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "0")

    with _client() as client:
        for _ in range(5):
            resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers("sk-bad"))
            assert resp.status_code == 401


def test_admin_api_unaffected_by_mcp_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS", raising=False)

    with _client() as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/admin/tools").status_code == 200


def test_both_keys_work_during_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "client-a:sk-old,client-a:sk-new")
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "x")
    labels = {"tool": "probe_ping", "caller": "client-a", "status": "ok"}
    before = metric_value(TOOL_CALLS, **labels)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "probe_ping"}}

    with _client() as client:
        for token in ("sk-old", "sk-new"):
            resp = client.post("/mcp/", json=call, headers=_mcp_headers(token))
            assert resp.status_code == 200

    assert metric_value(TOOL_CALLS, **labels) == before + 2


def test_token_in_development_is_still_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS", raising=False)

    with _client() as client:
        resp = client.post("/mcp/", json=_mcp_request(), headers=_mcp_headers("sk-any"))
        assert resp.status_code == 401

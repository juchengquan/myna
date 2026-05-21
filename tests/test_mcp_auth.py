from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myna.config import get_settings
from myna.main import create_app


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


def test_admin_api_unaffected_by_mcp_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS", raising=False)

    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/admin/tools").status_code == 200

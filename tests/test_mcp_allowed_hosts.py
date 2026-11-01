from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myna.config import get_settings
from myna.main import create_app

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS", raising=False)
    monkeypatch.delenv("MYNA_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MYNA_MCP_ALLOWED_ORIGINS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _initialize(*, host: str, origin: str | None = None) -> int:
    headers = {"Accept": "application/json, text/event-stream", "Host": host}
    if origin:
        headers["Origin"] = origin
    with TestClient(create_app()) as client:
        return client.post("/mcp/", json=INITIALIZE, headers=headers).status_code


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_local_hosts_allowed_by_default(host: str) -> None:
    assert _initialize(host=host) == 200


def test_remote_host_rejected_by_default() -> None:
    assert _initialize(host="myna.example.com") == 421


def test_configured_host_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_MCP_ALLOWED_HOSTS", '["myna.example.com"]')

    assert _initialize(host="myna.example.com") == 200
    assert _initialize(host="other.example.com") == 421


def test_unknown_origin_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _initialize(host="localhost:8000", origin="https://evil.example.com") == 403


def test_configured_origin_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_MCP_ALLOWED_ORIGINS", '["https://app.example.com"]')

    assert _initialize(host="localhost:8000", origin="https://app.example.com") == 200

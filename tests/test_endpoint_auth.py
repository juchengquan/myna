from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myna.config import get_settings
from myna.main import create_app

ENDPOINTS = [
    ("/metrics", "MYNA_METRICS_API_KEY"),
    ("/api/admin/tools", "MYNA_ADMIN_API_KEY"),
]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _status(path: str, token: str | None = None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with TestClient(create_app()) as client:
        return client.get(path, headers=headers).status_code


@pytest.mark.parametrize(("path", "setting"), ENDPOINTS)
@pytest.mark.parametrize("key", [None, ""])
def test_development_is_open_when_key_unset(
    monkeypatch: pytest.MonkeyPatch, path: str, setting: str, key: str | None
) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    if key is None:
        monkeypatch.delenv(setting, raising=False)
    else:
        monkeypatch.setenv(setting, key)

    assert _status(path) == 200


@pytest.mark.parametrize(("path", "setting"), ENDPOINTS)
@pytest.mark.parametrize("key", [None, ""])
def test_production_fails_closed_when_key_unset(
    monkeypatch: pytest.MonkeyPatch, path: str, setting: str, key: str | None
) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    if key is None:
        monkeypatch.delenv(setting, raising=False)
    else:
        monkeypatch.setenv(setting, key)

    assert _status(path) == 500


@pytest.mark.parametrize(("path", "setting"), ENDPOINTS)
def test_configured_key_is_required(
    monkeypatch: pytest.MonkeyPatch, path: str, setting: str
) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv(setting, "secret")

    assert _status(path) == 401
    assert _status(path, "wrong") == 401
    assert _status(path, "secret") == 200

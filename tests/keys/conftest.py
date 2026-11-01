from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient

from myna.config import get_settings
from myna.keys.migrate import alembic_config, upgrade
from myna.main import create_app

KEY_ADMIN = {"Authorization": "Bearer key-admin-secret"}
POSTGRES_URL = os.environ.get("MYNA_TEST_POSTGRES_URL")


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(params=["sqlite", "postgres"])
def database_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path}/keys.db"
    else:
        if not POSTGRES_URL:
            pytest.skip("MYNA_TEST_POSTGRES_URL isn't set")
        url = POSTGRES_URL
        command.downgrade(alembic_config(url), "base")
    upgrade(url)
    return url


@pytest.fixture()
def key_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> str:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_ADMIN_API_KEY", "admin-secret")
    monkeypatch.setenv("MYNA_METRICS_API_KEY", "metrics-secret")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "env-client:sk-env")
    monkeypatch.setenv("MYNA_DATABASE_URL", database_url)
    monkeypatch.setenv("MYNA_KEY_ADMIN_API_KEY", "key-admin-secret")
    return database_url


@pytest.fixture()
def make_client() -> Callable[[], TestClient]:
    def make() -> TestClient:
        return TestClient(create_app(), base_url="http://localhost:8000")

    return make


@pytest.fixture()
def client(key_env: str, make_client: Callable[[], TestClient]) -> Iterator[TestClient]:
    with make_client() as test_client:
        yield test_client

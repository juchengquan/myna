from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import ValidationError

from myna.config import Settings
from myna.keys.migrate import main as migrate_main
from myna.keys.store import KEY_PREFIX, create_engine, normalize_database_url
from myna.observability import RATE_LIMIT_HITS, TOOL_CALLS, metric_value
from tests.keys.conftest import KEY_ADMIN

PING = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ping"}}


def _call_mcp(client: TestClient, token: str) -> int:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    return client.post("/mcp/", json=PING, headers=headers).status_code


def _create(client: TestClient, name: str = "client-a", **body: Any) -> dict[str, Any]:
    resp = client.post("/api/admin/keys", json={"name": name, **body}, headers=KEY_ADMIN)
    assert resp.status_code == 201, resp.text
    result: dict[str, Any] = resp.json()
    return result


def _listed(client: TestClient, key_id: str) -> dict[str, Any]:
    keys = client.get("/api/admin/keys", headers=KEY_ADMIN).json()["keys"]
    return next(k for k in keys if k["id"] == key_id)


def _advance(client: TestClient, delta: timedelta) -> None:
    moved = datetime.now(UTC) + delta
    client.app.state.key_store.now = lambda: moved  # type: ignore[attr-defined]


def test_created_key_works_and_is_attributed_to_its_name(client: TestClient) -> None:
    labels = {"tool": "ping", "caller": "metrics-name", "status": "ok"}
    before = metric_value(TOOL_CALLS, **labels)

    created = _create(client, name="metrics-name")

    assert created["key"].startswith(KEY_PREFIX)
    assert _call_mcp(client, created["key"]) == 200
    assert metric_value(TOOL_CALLS, **labels) == before + 1


def test_secret_is_only_shown_once_and_only_a_hash_is_stored(
    client: TestClient, key_env: str
) -> None:
    created = _create(client)

    listing = client.get("/api/admin/keys", headers=KEY_ADMIN).text
    assert created["key"] not in listing
    assert "key_hash" not in listing

    async def stored_rows() -> list[Any]:
        engine = create_engine(key_env)
        async with engine.connect() as connection:
            rows = (await connection.execute(sa.text("SELECT * FROM api_keys"))).all()
        await engine.dispose()
        return list(rows)

    rows = asyncio.run(stored_rows())
    assert created["key"] not in str(rows)
    assert hashlib.sha256(created["key"].encode()).hexdigest() in str(rows)


def test_revoked_key_is_refused_on_the_next_request(client: TestClient) -> None:
    created = _create(client)
    assert _call_mcp(client, created["key"]) == 200

    resp = client.delete(f"/api/admin/keys/{created['id']}", headers=KEY_ADMIN)

    assert resp.status_code == 204
    assert _call_mcp(client, created["key"]) == 401
    assert _listed(client, created["id"])["status"] == "revoked"


def test_revoking_on_one_instance_applies_to_another(
    key_env: str, make_client: Callable[[], TestClient]
) -> None:
    with make_client() as first, make_client() as second:
        created = _create(first)
        assert _call_mcp(second, created["key"]) == 200

        first.delete(f"/api/admin/keys/{created['id']}", headers=KEY_ADMIN)

        assert _call_mcp(second, created["key"]) == 401


def test_revoking_twice_keeps_the_first_time(client: TestClient) -> None:
    created = _create(client)
    client.delete(f"/api/admin/keys/{created['id']}", headers=KEY_ADMIN)
    first = _listed(client, created["id"])["revoked_at"]

    _advance(client, timedelta(hours=1))
    resp = client.delete(f"/api/admin/keys/{created['id']}", headers=KEY_ADMIN)

    assert resp.status_code == 204
    assert _listed(client, created["id"])["revoked_at"] == first


def test_revoking_an_unknown_key_is_a_404(client: TestClient) -> None:
    assert client.delete("/api/admin/keys/nope", headers=KEY_ADMIN).status_code == 404


def test_keys_expire_after_90_days_by_default(client: TestClient) -> None:
    created = _create(client)
    expires = datetime.fromisoformat(created["expires_at"])
    assert expires - datetime.fromisoformat(created["created_at"]) == timedelta(days=90)

    _advance(client, timedelta(days=89))
    assert _call_mcp(client, created["key"]) == 200

    _advance(client, timedelta(days=91))
    assert _call_mcp(client, created["key"]) == 401
    assert _listed(client, created["id"])["status"] == "expired"


def test_expiry_can_be_changed_or_turned_off(client: TestClient) -> None:
    short = _create(client, expires_in_days=1)
    forever = _create(client, name="client-b", expires_in_days=None)
    assert forever["expires_at"] is None

    _advance(client, timedelta(days=4000))

    assert _call_mcp(client, short["key"]) == 401
    assert _call_mcp(client, forever["key"]) == 200


@pytest.mark.parametrize(
    "body",
    [
        {"name": "client a"},
        {"name": "anonymous"},
        {"name": ""},
        {"name": "x" * 65},
        {"name": "client-a", "expires_in_days": 0},
        {"name": "client-a", "expires_in_days": 3651},
    ],
)
def test_invalid_requests_are_rejected(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/api/admin/keys", json=body, headers=KEY_ADMIN).status_code == 422


def test_names_may_repeat_for_rotation(client: TestClient) -> None:
    labels = {"tool": "ping", "caller": "rotating", "status": "ok"}
    before = metric_value(TOOL_CALLS, **labels)
    old = _create(client, name="rotating")
    new = _create(client, name="rotating")

    assert _call_mcp(client, old["key"]) == 200
    assert _call_mcp(client, new["key"]) == 200
    assert metric_value(TOOL_CALLS, **labels) == before + 2


def test_last_used_is_written_at_most_once_a_minute(client: TestClient) -> None:
    created = _create(client)
    assert _listed(client, created["id"])["last_used_at"] is None

    _call_mcp(client, created["key"])
    first = _listed(client, created["id"])["last_used_at"]
    assert first is not None

    _advance(client, timedelta(seconds=30))
    _call_mcp(client, created["key"])
    assert _listed(client, created["id"])["last_used_at"] == first

    _advance(client, timedelta(seconds=90))
    _call_mcp(client, created["key"])
    assert _listed(client, created["id"])["last_used_at"] != first


def test_cap_on_active_keys(
    monkeypatch: pytest.MonkeyPatch, key_env: str, make_client: Callable[[], TestClient]
) -> None:
    monkeypatch.setenv("MYNA_MAX_ACTIVE_KEYS", "1")

    with make_client() as client:
        first = _create(client, expires_in_days=1)
        body = {"name": "client-b"}
        assert client.post("/api/admin/keys", json=body, headers=KEY_ADMIN).status_code == 409

        # An expired key no longer counts.
        _advance(client, timedelta(days=2))
        second = _create(client, name="client-b")

        # Neither does a revoked one.
        client.delete(f"/api/admin/keys/{second['id']}", headers=KEY_ADMIN)
        _create(client, name="client-c")
        assert _call_mcp(client, first["key"]) == 401


def test_env_keys_still_work_alongside_database_keys(client: TestClient) -> None:
    assert _call_mcp(client, "sk-env") == 200
    assert _call_mcp(client, _create(client)["key"]) == 200
    assert _call_mcp(client, "sk-wrong") == 401
    assert _call_mcp(client, KEY_PREFIX + "wrong") == 401


def test_database_keys_are_rate_limited_by_name(
    monkeypatch: pytest.MonkeyPatch, key_env: str, make_client: Callable[[], TestClient]
) -> None:
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "2")

    with make_client() as client:
        one = _create(client, name="shared")
        two = _create(client, name="shared")
        assert _call_mcp(client, one["key"]) == 200
        assert _call_mcp(client, two["key"]) == 200
        assert _call_mcp(client, one["key"]) == 429


def test_only_the_key_admin_key_opens_the_routes(client: TestClient) -> None:
    for headers in (
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Bearer admin-secret"},
        {"Authorization": "Bearer sk-env"},
    ):
        assert client.get("/api/admin/keys", headers=headers).status_code == 401
    wrong = {"Authorization": "Bearer key-admin-secret"}
    assert client.get("/api/admin/tools", headers=wrong).status_code == 401
    assert client.get("/api/admin/keys", headers=KEY_ADMIN).status_code == 200


def test_a_created_key_cannot_manage_keys(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {_create(client)['key']}"}
    assert client.get("/api/admin/keys", headers=headers).status_code == 401


def test_failed_key_admin_attempts_are_throttled(
    monkeypatch: pytest.MonkeyPatch, key_env: str, make_client: Callable[[], TestClient]
) -> None:
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "2")
    before = metric_value(RATE_LIMIT_HITS, key_kind="auth_failure")
    wrong = {"Authorization": "Bearer wrong"}

    with make_client() as client:
        statuses = [client.get("/api/admin/keys", headers=wrong).status_code for _ in range(3)]
        assert statuses == [401, 401, 429]
        # The right key still works from a throttled address.
        assert client.get("/api/admin/keys", headers=KEY_ADMIN).status_code == 200

    assert metric_value(RATE_LIMIT_HITS, key_kind="auth_failure") == before + 1


def test_create_and_revoke_are_audit_logged_without_the_secret(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="myna.audit")

    created = _create(client)
    client.delete(f"/api/admin/keys/{created['id']}", headers=KEY_ADMIN)

    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "myna.audit"]
    events = {line["event"]: line for line in lines}
    assert events["key_created"]["key_id"] == created["id"]
    assert events["key_created"]["name"] == "client-a"
    assert events["key_revoked"]["key_id"] == created["id"]
    assert created["key"] not in caplog.text


def test_routes_do_not_exist_without_the_key_admin_key(
    monkeypatch: pytest.MonkeyPatch, key_env: str, make_client: Callable[[], TestClient]
) -> None:
    monkeypatch.delenv("MYNA_KEY_ADMIN_API_KEY")

    with make_client() as client:
        assert client.get("/api/admin/keys", headers=KEY_ADMIN).status_code == 404


def test_without_a_database_prefixed_tokens_are_a_plain_401(
    monkeypatch: pytest.MonkeyPatch, make_client: Callable[[], TestClient]
) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "env-client:sk-env")
    monkeypatch.delenv("MYNA_DATABASE_URL", raising=False)
    monkeypatch.delenv("MYNA_KEY_ADMIN_API_KEY", raising=False)

    with make_client() as client:
        assert _call_mcp(client, KEY_PREFIX + "anything") == 401
        assert client.get("/api/admin/keys", headers=KEY_ADMIN).status_code == 404


def test_development_with_a_database_is_not_anonymous(
    monkeypatch: pytest.MonkeyPatch, key_env: str, make_client: Callable[[], TestClient]
) -> None:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.delenv("MYNA_MCP_API_KEYS")
    headers = {"Accept": "application/json, text/event-stream"}

    with make_client() as client:
        assert client.post("/mcp/", json=PING, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "dead_url",
    ["sqlite:////nonexistent-directory/keys.db", "postgresql://u:p@127.0.0.1:1/none"],
)
def test_database_outage(
    monkeypatch: pytest.MonkeyPatch, make_client: Callable[[], TestClient], dead_url: str
) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "env-client:sk-env")
    monkeypatch.setenv("MYNA_DATABASE_URL", dead_url)
    monkeypatch.setenv("MYNA_KEY_ADMIN_API_KEY", "key-admin-secret")

    # Starting up during an outage still works, for clients with env keys.
    with make_client() as client:
        assert _call_mcp(client, "sk-env") == 200
        assert _call_mcp(client, "sk-wrong") == 401
        assert _call_mcp(client, KEY_PREFIX + "anything") == 503
        assert client.get("/api/admin/keys", headers=KEY_ADMIN).status_code == 503


def test_startup_stops_if_migrations_are_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, make_client: Callable[[], TestClient]
) -> None:
    monkeypatch.setenv("MYNA_ENV", "production")
    monkeypatch.setenv("MYNA_DATABASE_URL", f"sqlite:///{tmp_path}/empty.db")

    with pytest.raises(RuntimeError, match="Run myna-migrate"), make_client():
        pass


def test_migrate_command_is_repeatable(key_env: str, capsys: pytest.CaptureFixture[str]) -> None:
    migrate_main()
    migrate_main()
    assert "up to date" in capsys.readouterr().out


def test_migrate_command_needs_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MYNA_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="MYNA_DATABASE_URL isn't set"):
        migrate_main()


def test_migrate_failure_does_not_print_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_DATABASE_URL", "postgresql://u:hunter2@127.0.0.1:1/none")
    with pytest.raises(SystemExit) as exc_info:
        migrate_main()
    assert "hunter2" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("url", "normalized"),
    [
        ("sqlite:///./myna.db", "sqlite+aiosqlite:///./myna.db"),
        ("postgres://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("postgresql://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("postgresql+asyncpg://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
    ],
)
def test_database_urls_use_async_drivers(url: str, normalized: str) -> None:
    assert normalize_database_url(url) == normalized


@pytest.mark.parametrize("url", ["mysql://u:hunter2@h/d", "not a url hunter2"])
def test_bad_database_url_stops_startup_without_printing_it(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("MYNA_DATABASE_URL", url)
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "MYNA_DATABASE_URL" in str(exc_info.value)
    assert "hunter2" not in str(exc_info.value)


def test_key_admin_key_needs_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MYNA_DATABASE_URL", raising=False)
    monkeypatch.setenv("MYNA_KEY_ADMIN_API_KEY", "key-admin-secret")
    with pytest.raises(ValidationError, match="MYNA_KEY_ADMIN_API_KEY needs MYNA_DATABASE_URL"):
        Settings()


def test_blank_settings_mean_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_DATABASE_URL", "")
    monkeypatch.setenv("MYNA_KEY_ADMIN_API_KEY", "")
    settings = Settings()
    assert settings.database_url is None
    assert settings.key_admin_api_key is None

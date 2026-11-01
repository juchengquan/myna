"""Tests over the real Streamable HTTP transport.

In-process tests can't catch transport behavior, such as how a tool asks
the client a question or streams progress. These tests run a server on a local port and connect with
the SDK's client, under a timeout so a hang fails instead of blocking CI.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any, cast

import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST, ElicitResult, TextContent

from myna.config import get_settings
from myna.main import create_app
from myna.observability import TOOL_CALLS, metric_value

TIMEOUT_SECONDS = 10.0
MODERN_PROTOCOL = "2026-07-28"
MISSING_CLIENT_CAPABILITY = -32021


@contextlib.contextmanager
def _live_server(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stateless: bool,
    api_keys: str = "",
    env: dict[str, str] | None = None,
) -> Iterator[str]:
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", api_keys)
    monkeypatch.setenv("MYNA_MCP_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("MYNA_MCP_STATELESS", str(stateless).lower())
    get_settings.cache_clear()

    config = uvicorn.Config(create_app(), log_level="warning", timeout_graceful_shutdown=2)
    server = uvicorn.Server(config)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + TIMEOUT_SECONDS
            while not server.started:
                assert time.monotonic() < deadline, "server did not start"
                time.sleep(0.01)
            yield f"http://127.0.0.1:{port}/mcp/"
        finally:
            server.should_exit = True
            thread.join(timeout=TIMEOUT_SECONDS)
            get_settings.cache_clear()


@pytest.fixture()
def stateless_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with _live_server(monkeypatch, stateless=True) as url:
        yield url


@pytest.fixture()
def stateful_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with _live_server(monkeypatch, stateless=False) as url:
        yield url


@contextlib.asynccontextmanager
async def _session(
    url: str, headers: dict[str, str] | None = None, **callbacks: Any
) -> AsyncIterator[ClientSession]:
    """A legacy connection: the `initialize` handshake, up to spec 2025-11-25."""
    async with (
        asyncio.timeout(TIMEOUT_SECONDS),
        httpx2.AsyncClient(headers=headers, follow_redirects=False) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write),
        ClientSession(read, write, **callbacks) as session,
    ):
        await session.initialize()
        yield session


@contextlib.asynccontextmanager
async def _modern_client(
    url: str, headers: dict[str, str] | None = None, **callbacks: Any
) -> AsyncIterator[Client]:
    """A modern connection: sessionless, spec 2026-07-28."""
    async with (
        asyncio.timeout(TIMEOUT_SECONDS),
        httpx2.AsyncClient(headers=headers, follow_redirects=False) as http_client,
        Client(streamable_http_client(url, http_client=http_client), **callbacks) as client,
    ):
        assert client.protocol_version == MODERN_PROTOCOL
        yield client


async def _on_elicitation(context: Any, params: Any) -> ElicitResult:
    return ElicitResult(action="accept", content={"confirmed": True, "note": "ok"})


def _text(result: Any) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


@pytest.mark.parametrize("fixture", ["stateless_url", "stateful_url"])
async def test_tools_are_offered_in_both_modes(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    url = cast(str, request.getfixturevalue(fixture))
    async with _session(url) as session:
        tools = {t.name for t in (await session.list_tools()).tools}

    assert {"ping", "stream_count", "confirm_action"} <= tools


async def test_stateless_legacy_elicitation_fails_without_hanging(stateless_url: str) -> None:
    async with _session(stateless_url, elicitation_callback=_on_elicitation) as session:
        with pytest.raises(MCPError) as exc_info:
            await session.call_tool("confirm_action", {"action": "deploy"})

    assert exc_info.value.error.code == INVALID_REQUEST


async def test_mount_path_works_without_trailing_slash(stateless_url: str) -> None:
    # `_session` doesn't follow redirects, so this fails on a 307.
    async with _session(stateless_url.rstrip("/")) as session:
        result = await session.call_tool("ping", {})

    assert _text(result) == "pong"


@pytest.mark.parametrize("fixture", ["stateless_url", "stateful_url"])
async def test_legacy_stream_count_delivers_progress(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    url = cast(str, request.getfixturevalue(fixture))
    progress: list[float] = []

    async def on_progress(value: float, total: float | None, message: str | None) -> None:
        progress.append(value)

    async with _session(url) as session:
        result = await session.call_tool(
            "stream_count", {"n": 2, "delay_ms": 0}, progress_callback=on_progress
        )

    assert _text(result) == "counted to 2"
    assert progress == [1.0, 2.0]


async def test_stateful_legacy_elicitation_round_trip(stateful_url: str) -> None:
    async with _session(stateful_url, elicitation_callback=_on_elicitation) as session:
        result = await session.call_tool("confirm_action", {"action": "deploy"})

    assert not result.is_error
    assert "approved" in _text(result)


async def test_stateful_legacy_elicitation_refused_without_client_capability(
    stateful_url: str,
) -> None:
    async with _session(stateful_url) as session:
        with pytest.raises(MCPError) as exc_info:
            await session.call_tool("confirm_action", {"action": "deploy"})

    assert exc_info.value.error.code == MISSING_CLIENT_CAPABILITY


@pytest.mark.parametrize("fixture", ["stateless_url", "stateful_url"])
async def test_modern_stream_count_delivers_progress(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    url = cast(str, request.getfixturevalue(fixture))
    progress: list[float] = []

    async def on_progress(value: float, total: float | None, message: str | None) -> None:
        progress.append(value)

    async with _modern_client(url) as client:
        result = await client.call_tool(
            "stream_count", {"n": 2, "delay_ms": 0}, progress_callback=on_progress
        )

    assert _text(result) == "counted to 2"
    assert progress == [1.0, 2.0]


@pytest.mark.parametrize("fixture", ["stateless_url", "stateful_url"])
async def test_modern_elicitation_round_trip(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    url = cast(str, request.getfixturevalue(fixture))
    async with _modern_client(url, elicitation_callback=_on_elicitation) as client:
        result = await client.call_tool("confirm_action", {"action": "deploy"})

    assert not result.is_error
    assert "approved" in _text(result)


@pytest.mark.parametrize("stateless", [True, False])
async def test_caller_reaches_metrics_on_both_protocol_eras(
    monkeypatch: pytest.MonkeyPatch, stateless: bool
) -> None:
    token = {"Authorization": "Bearer sk-wire"}
    labels = {"tool": "ping", "caller": "wire-client", "status": "ok"}
    before = metric_value(TOOL_CALLS, **labels)

    api_keys = "wire-client:sk-wire"
    with _live_server(monkeypatch, stateless=stateless, api_keys=api_keys) as url:
        async with _session(url, headers=token) as session:
            await session.call_tool("ping", {})
        async with _modern_client(url, headers=token) as client:
            await client.call_tool("ping", {})

    assert metric_value(TOOL_CALLS, **labels) == before + 2


async def test_tool_errors_reach_the_client(stateless_url: str) -> None:
    async with _session(stateless_url) as session:
        result = await session.call_tool("stream_count", {"n": 0})

    assert result.is_error
    assert "n must be between 1 and 20" in _text(result)


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "wire-test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


async def _status_counts(tool: str) -> tuple[float, float]:
    labels = {"tool": tool, "caller": "anonymous"}
    return (
        metric_value(TOOL_CALLS, status="ok", **labels),
        metric_value(TOOL_CALLS, status="input_required", **labels),
    )


async def test_modern_question_is_not_counted_as_a_completed_call(stateful_url: str) -> None:
    ok_before, asked_before = await _status_counts("confirm_action")

    async with _modern_client(stateful_url, elicitation_callback=_on_elicitation) as client:
        await client.call_tool("confirm_action", {"action": "deploy"})

    ok_after, asked_after = await _status_counts("confirm_action")
    assert ok_after == ok_before + 1
    assert asked_after == asked_before + 1


async def test_legacy_question_is_counted_once(stateful_url: str) -> None:
    ok_before, asked_before = await _status_counts("confirm_action")

    async with _session(stateful_url, elicitation_callback=_on_elicitation) as session:
        await session.call_tool("confirm_action", {"action": "deploy"})

    ok_after, asked_after = await _status_counts("confirm_action")
    assert ok_after == ok_before + 1
    assert asked_after == asked_before


async def test_max_sessions_refuses_new_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"MYNA_MCP_MAX_SESSIONS": "1"}
    with _live_server(monkeypatch, stateless=False, env=env) as url:
        async with (
            asyncio.timeout(TIMEOUT_SECONDS),
            httpx2.AsyncClient(headers=MCP_HEADERS) as http,
        ):
            first = await http.post(url, json=INITIALIZE)
            second = await http.post(url, json=INITIALIZE)

    assert first.status_code == 200
    assert second.status_code == 503


async def test_idle_session_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"MYNA_MCP_SESSION_IDLE_TIMEOUT_SECONDS": "0.2"}
    with _live_server(monkeypatch, stateless=False, env=env) as url:
        async with (
            asyncio.timeout(TIMEOUT_SECONDS),
            httpx2.AsyncClient(headers=MCP_HEADERS) as http,
        ):
            opened = await http.post(url, json=INITIALIZE)
            session = {
                "Mcp-Session-Id": opened.headers["mcp-session-id"],
                "MCP-Protocol-Version": "2025-11-25",
            }
            # Any request restarts the idle clock, so wait once and then check.
            await asyncio.sleep(1.0)
            ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
            expired = await http.post(url, json=ping, headers=session)

    assert expired.status_code == 404

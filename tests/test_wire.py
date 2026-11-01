"""Tests over the real Streamable HTTP transport.

In-process tests can't catch transport behavior: the sampling and
elicitation tools pass all of those in stateless mode and still hang for
a real client. These tests run a server on a local port and connect with
the SDK's client, under a timeout so a hang fails instead of blocking CI.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    CreateMessageResult,
    ElicitResult,
    LoggingMessageNotificationParams,
    TextContent,
)

from myna.config import get_settings
from myna.main import create_app

TIMEOUT_SECONDS = 10.0


@contextlib.contextmanager
def _live_server(monkeypatch: pytest.MonkeyPatch, *, stateless: bool) -> Iterator[str]:
    monkeypatch.setenv("MYNA_ENV", "development")
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "{}")
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
async def _session(url: str, **callbacks: Any) -> AsyncIterator[ClientSession]:
    async with (
        asyncio.timeout(TIMEOUT_SECONDS),
        httpx.AsyncClient(follow_redirects=False) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write, _),
        ClientSession(read, write, **callbacks) as session,
    ):
        await session.initialize()
        yield session


async def _on_sampling(context: Any, params: Any) -> CreateMessageResult:
    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text="client summary"),
        model="test",
    )


async def _on_elicitation(context: Any, params: Any) -> ElicitResult:
    return ElicitResult(action="accept", content={"confirmed": True, "note": "ok"})


def _text(result: Any) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


async def test_stateless_omits_interactive_tools(stateless_url: str) -> None:
    async with _session(stateless_url) as session:
        tools = {t.name for t in (await session.list_tools()).tools}
        assert "ping" in tools
        assert "stream_count" in tools
        assert "summarize_via_sampling" not in tools
        assert "confirm_action" not in tools


async def test_mount_path_works_without_trailing_slash(stateless_url: str) -> None:
    # `_session` doesn't follow redirects, so this fails on a 307.
    async with _session(stateless_url.rstrip("/")) as session:
        result = await session.call_tool("ping", {})

    assert _text(result) == "pong"


async def test_stateless_stream_count_delivers_logs(stateless_url: str) -> None:
    logs: list[Any] = []

    async def on_log(params: LoggingMessageNotificationParams) -> None:
        logs.append(params.data)

    async with _session(stateless_url, logging_callback=on_log) as session:
        result = await session.call_tool("stream_count", {"n": 2, "delay_ms": 0})

    assert _text(result) == "counted to 2"
    assert logs == ["tick 1", "tick 2"]


async def test_stateful_stream_count_delivers_progress(stateful_url: str) -> None:
    progress: list[float] = []

    async def on_progress(value: float, total: float | None, message: str | None) -> None:
        progress.append(value)

    async with _session(stateful_url) as session:
        await session.call_tool(
            "stream_count", {"n": 2, "delay_ms": 0}, progress_callback=on_progress
        )

    assert progress == [1.0, 2.0]


async def test_stateful_sampling_round_trip(stateful_url: str) -> None:
    async with _session(stateful_url, sampling_callback=_on_sampling) as session:
        result = await session.call_tool("summarize_via_sampling", {"text": "hello"})

    assert not result.isError
    assert _text(result) == "client summary"


async def test_stateful_elicitation_round_trip(stateful_url: str) -> None:
    async with _session(stateful_url, elicitation_callback=_on_elicitation) as session:
        result = await session.call_tool("confirm_action", {"action": "deploy"})

    assert not result.isError
    assert "approved" in _text(result)


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("summarize_via_sampling", {"text": "hello"}, "sampling"),
        ("confirm_action", {"action": "deploy"}, "elicitation"),
    ],
)
async def test_stateful_tools_error_without_client_capability(
    stateful_url: str, tool: str, arguments: dict[str, str], expected: str
) -> None:
    async with _session(stateful_url) as session:
        result = await session.call_tool(tool, arguments)

    assert result.isError
    assert expected in _text(result)

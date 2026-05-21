from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from myna.mcp_server import build_mcp
from myna.observability import TOOL_CALLS, TOOL_DURATION


def test_metrics_endpoint_serves_prometheus_format(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "myna_tool_calls_total" in body
    assert "myna_tool_call_duration_seconds" in body


@pytest.mark.asyncio
async def test_tool_call_increments_metrics() -> None:
    mcp = build_mcp()
    before_ok = _counter("ping", "anonymous", "ok")
    before_hist = _hist_count("ping")

    await mcp.call_tool("ping", {})

    assert _counter("ping", "anonymous", "ok") == before_ok + 1
    assert _hist_count("ping") == before_hist + 1


@pytest.mark.asyncio
async def test_failed_tool_call_recorded_as_error() -> None:
    mcp = build_mcp()
    before_err = _counter("stream_count", "anonymous", "error")

    # stream_count's input validation raises before any Context access.
    with pytest.raises(ToolError):
        await mcp.call_tool("stream_count", {"n": 0, "delay_ms": 0})

    assert _counter("stream_count", "anonymous", "error") == before_err + 1


def _counter(tool: str, caller: str, status: str) -> float:
    return float(TOOL_CALLS.labels(tool=tool, caller=caller, status=status)._value.get())  # type: ignore[attr-defined]


def _hist_count(tool: str) -> float:
    # Internal `_buckets` are non-cumulative — summing them yields the
    # total observation count.
    buckets = TOOL_DURATION.labels(tool=tool)._buckets  # type: ignore[attr-defined]
    return float(sum(b.get() for b in buckets))

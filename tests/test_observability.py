from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from myna.mcp_server import build_mcp
from myna.observability import (
    PROMPT_DURATION,
    PROMPT_GETS,
    RESOURCE_DURATION,
    RESOURCE_READS,
    TOOL_CALLS,
    TOOL_DURATION,
    histogram_count,
    metric_value,
)


def test_metrics_endpoint_serves_prometheus_format(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    for name in (
        "myna_tool_calls_total",
        "myna_tool_call_duration_seconds",
        "myna_resource_reads_total",
        "myna_resource_read_duration_seconds",
        "myna_prompt_gets_total",
        "myna_prompt_get_duration_seconds",
    ):
        assert name in body, f"missing {name} in /metrics"


@pytest.mark.asyncio
async def test_tool_call_increments_metrics() -> None:
    mcp = build_mcp()
    before_ok = metric_value(TOOL_CALLS, tool="ping", caller="anonymous", status="ok")
    before_hist = histogram_count(TOOL_DURATION, tool="ping")

    await mcp.call_tool("ping", {})

    assert metric_value(TOOL_CALLS, tool="ping", caller="anonymous", status="ok") == before_ok + 1
    assert histogram_count(TOOL_DURATION, tool="ping") == before_hist + 1


@pytest.mark.asyncio
async def test_failed_tool_call_recorded_as_error() -> None:
    mcp = build_mcp()
    before_err = metric_value(
        TOOL_CALLS, tool="stream_count", caller="anonymous", status="error"
    )

    with pytest.raises(ToolError):
        await mcp.call_tool("stream_count", {"n": 0, "delay_ms": 0})

    assert (
        metric_value(TOOL_CALLS, tool="stream_count", caller="anonymous", status="error")
        == before_err + 1
    )


@pytest.mark.asyncio
async def test_resource_read_increments_metrics() -> None:
    mcp = build_mcp()
    uri = "myna://server-info"
    before = metric_value(RESOURCE_READS, uri=uri, caller="anonymous", status="ok")
    before_hist = histogram_count(RESOURCE_DURATION, uri=uri)

    list(await mcp.read_resource(uri))

    assert metric_value(RESOURCE_READS, uri=uri, caller="anonymous", status="ok") == before + 1
    assert histogram_count(RESOURCE_DURATION, uri=uri) == before_hist + 1


@pytest.mark.asyncio
async def test_prompt_get_increments_metrics() -> None:
    mcp = build_mcp()
    before = metric_value(PROMPT_GETS, name="summarize", caller="anonymous", status="ok")
    before_hist = histogram_count(PROMPT_DURATION, name="summarize")

    await mcp.get_prompt("summarize", {"text": "hello", "sentences": "1"})

    after = metric_value(PROMPT_GETS, name="summarize", caller="anonymous", status="ok")
    assert after == before + 1
    assert histogram_count(PROMPT_DURATION, name="summarize") == before_hist + 1

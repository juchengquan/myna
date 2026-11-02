from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError

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
    render_metrics,
)
from tests.probes import INFO_URI


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
    labels = {"tool": "probe_ping", "caller": "anonymous", "status": "ok"}
    before_ok = metric_value(TOOL_CALLS, **labels)
    before_hist = histogram_count(TOOL_DURATION, tool="probe_ping")

    await mcp.call_tool("probe_ping", {})

    assert metric_value(TOOL_CALLS, **labels) == before_ok + 1
    assert histogram_count(TOOL_DURATION, tool="probe_ping") == before_hist + 1


@pytest.mark.asyncio
async def test_failed_tool_call_recorded_as_error() -> None:
    mcp = build_mcp()
    before_err = metric_value(
        TOOL_CALLS, tool="probe_fail", caller="anonymous", status="error"
    )

    with pytest.raises(ToolError):
        await mcp.call_tool("probe_fail", {})

    assert (
        metric_value(TOOL_CALLS, tool="probe_fail", caller="anonymous", status="error")
        == before_err + 1
    )


@pytest.mark.asyncio
async def test_resource_read_increments_metrics() -> None:
    mcp = build_mcp()
    uri = INFO_URI
    before = metric_value(RESOURCE_READS, uri=uri, caller="anonymous", status="ok")
    before_hist = histogram_count(RESOURCE_DURATION, uri=uri)

    list(await mcp.read_resource(uri))

    assert metric_value(RESOURCE_READS, uri=uri, caller="anonymous", status="ok") == before + 1
    assert histogram_count(RESOURCE_DURATION, uri=uri) == before_hist + 1


@pytest.mark.asyncio
async def test_prompt_get_increments_metrics() -> None:
    mcp = build_mcp()
    before = metric_value(PROMPT_GETS, name="probe_prompt", caller="anonymous", status="ok")
    before_hist = histogram_count(PROMPT_DURATION, name="probe_prompt")

    await mcp.get_prompt("probe_prompt", {"text": "hello"})

    after = metric_value(PROMPT_GETS, name="probe_prompt", caller="anonymous", status="ok")
    assert after == before + 1
    assert histogram_count(PROMPT_DURATION, name="probe_prompt") == before_hist + 1


@pytest.mark.asyncio
async def test_templated_resource_is_labelled_by_template() -> None:
    mcp = build_mcp()
    template = "example://items/{item}"

    @mcp.resource(template, name="example-item")
    def item(item: str) -> str:
        return item

    before = metric_value(RESOURCE_READS, uri=template, caller="anonymous", status="ok")

    list(await mcp.read_resource("example://items/first"))
    list(await mcp.read_resource("example://items/second"))

    assert metric_value(RESOURCE_READS, uri=template, caller="anonymous", status="ok") == before + 2
    assert 'uri="example://items/first"' not in render_metrics()[0].decode()


@pytest.mark.asyncio
async def test_unregistered_targets_are_labelled_unknown() -> None:
    mcp = build_mcp()
    before_tool = metric_value(TOOL_CALLS, tool="unknown", caller="anonymous", status="error")
    before_uri = metric_value(RESOURCE_READS, uri="unknown", caller="anonymous", status="error")
    before_prompt = metric_value(PROMPT_GETS, name="unknown", caller="anonymous", status="error")

    with pytest.raises(ToolError):
        await mcp.call_tool("no-such-tool", {})
    with pytest.raises(ResourceNotFoundError):
        await mcp.read_resource("myna://no-such-resource")
    with pytest.raises(ValueError):
        await mcp.get_prompt("no-such-prompt")

    assert (
        metric_value(TOOL_CALLS, tool="unknown", caller="anonymous", status="error")
        == before_tool + 1
    )
    assert (
        metric_value(RESOURCE_READS, uri="unknown", caller="anonymous", status="error")
        == before_uri + 1
    )
    assert (
        metric_value(PROMPT_GETS, name="unknown", caller="anonymous", status="error")
        == before_prompt + 1
    )
    payload = render_metrics()[0].decode()
    for name in ("no-such-tool", "no-such-resource", "no-such-prompt"):
        assert name not in payload

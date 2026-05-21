from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from myna.mcp_server import build_mcp
from myna.tracing import install_test_exporter


@pytest.fixture()
def exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    install_test_exporter(exp)
    return exp


@pytest.mark.asyncio
async def test_tool_call_emits_span(exporter: InMemorySpanExporter) -> None:
    mcp = build_mcp()
    await mcp.call_tool("ping", {})

    spans = exporter.get_finished_spans()
    tool_spans = [s for s in spans if s.name.startswith("mcp.tool.call")]
    assert tool_spans, "expected at least one mcp.tool.call span"

    span = tool_spans[-1]
    attrs = dict(span.attributes or {})
    assert attrs["mcp.tool.name"] == "ping"
    assert attrs["mcp.status"] == "ok"
    assert attrs["mcp.caller"] == "anonymous"
    assert isinstance(attrs["mcp.duration_ms"], float)


@pytest.mark.asyncio
async def test_failing_tool_call_marks_span_error(exporter: InMemorySpanExporter) -> None:
    mcp = build_mcp()
    with pytest.raises(ToolError):
        await mcp.call_tool("stream_count", {"n": 0, "delay_ms": 0})

    failing = [s for s in exporter.get_finished_spans() if "stream_count" in s.name]
    assert failing, "expected an mcp.tool.call span for stream_count"
    span = failing[-1]
    assert dict(span.attributes or {}).get("mcp.status") == "error"
    # OTel records the exception as a span event.
    event_names = {ev.name for ev in span.events}
    assert "exception" in event_names

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from myna.config import Settings
from myna.logging_config import configure_logging, inject_trace_context
from myna.main import create_app
from myna.mcp_server import build_mcp
from myna.tracing import install_test_exporter, setup_tracing


@pytest.fixture()
def exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    install_test_exporter(exp)
    return exp


@pytest.mark.asyncio
async def test_tool_call_emits_span(exporter: InMemorySpanExporter) -> None:
    mcp = build_mcp()
    await mcp.call_tool("ping", {})

    tool_spans = [s for s in exporter.get_finished_spans() if s.name.startswith("mcp.tool.call")]
    assert tool_spans
    attrs = dict(tool_spans[-1].attributes or {})
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
    assert failing
    span = failing[-1]
    assert dict(span.attributes or {}).get("mcp.status") == "error"
    assert "exception" in {ev.name for ev in span.events}


@pytest.mark.asyncio
async def test_resource_read_emits_span(exporter: InMemorySpanExporter) -> None:
    mcp = build_mcp()
    list(await mcp.read_resource("myna://server-info"))

    spans = [s for s in exporter.get_finished_spans() if s.name.startswith("mcp.resource.read")]
    assert spans
    attrs = dict(spans[-1].attributes or {})
    assert attrs["mcp.resource.uri"] == "myna://server-info"
    assert attrs["mcp.status"] == "ok"


@pytest.mark.asyncio
async def test_prompt_get_emits_span(exporter: InMemorySpanExporter) -> None:
    mcp = build_mcp()
    await mcp.get_prompt("summarize", {"text": "hi", "sentences": "1"})

    spans = [s for s in exporter.get_finished_spans() if s.name.startswith("mcp.prompt.get")]
    assert spans
    attrs = dict(spans[-1].attributes or {})
    assert attrs["mcp.prompt.name"] == "summarize"
    assert attrs["mcp.status"] == "ok"


def test_inject_trace_context_no_active_span_is_noop() -> None:
    out = inject_trace_context(None, "info", {"event": "x"})
    assert "trace_id" not in out
    assert "span_id" not in out


def test_inject_trace_context_adds_ids_when_span_active(
    exporter: InMemorySpanExporter,
) -> None:
    tracer = trace.get_tracer("myna.test")
    with tracer.start_as_current_span("test"):
        out = inject_trace_context(None, "info", {"event": "x"})
    assert "trace_id" in out and len(out["trace_id"]) == 32
    assert "span_id" in out and len(out["span_id"]) == 16


@pytest.mark.asyncio
async def test_audit_log_includes_trace_id(
    exporter: InMemorySpanExporter, caplog: pytest.LogCaptureFixture
) -> None:
    # Re-configure structlog so the inject_trace_context processor runs
    # in this test. The test's own caplog capture sees the emitted JSON.
    configure_logging("INFO")
    caplog.set_level(logging.INFO, logger="myna.audit")

    mcp = build_mcp()
    await mcp.call_tool("ping", {})

    audit_records = [
        json.loads(r.message)
        for r in caplog.records
        if r.name == "myna.audit" and r.message.startswith("{")
    ]
    assert audit_records, "expected an audit log record"
    last = audit_records[-1]
    assert last["event"] == "tool_call"
    assert "trace_id" in last
    assert "span_id" in last


def test_setup_tracing_disabled_installs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[object] = []
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)

    assert setup_tracing(Settings(otel_enabled=False)) is None
    assert installed == []


def test_setup_tracing_sets_resource_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[object] = []
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)

    settings = Settings(env="staging", otel_enabled=True, otel_service_name="myna-test")
    provider = setup_tracing(settings)

    assert provider is not None
    assert installed == [provider]
    attributes = provider.resource.attributes
    assert attributes["service.name"] == "myna-test"
    assert attributes["deployment.environment"] == "staging"
    provider.shutdown()


@pytest.mark.parametrize(
    ("endpoint", "exporter_type"),
    [(None, ConsoleSpanExporter), ("http://collector:4318/v1/traces", OTLPSpanExporter)],
)
def test_setup_tracing_picks_exporter_from_endpoint(
    monkeypatch: pytest.MonkeyPatch, endpoint: str | None, exporter_type: type
) -> None:
    exporters: list[object] = []

    def recording_processor(exporter: object) -> SimpleSpanProcessor:
        exporters.append(exporter)
        return SimpleSpanProcessor(InMemorySpanExporter())

    monkeypatch.setattr(trace, "set_tracer_provider", lambda provider: None)
    monkeypatch.setattr("myna.tracing.BatchSpanProcessor", recording_processor)
    monkeypatch.setattr("myna.tracing.SimpleSpanProcessor", recording_processor)

    settings = Settings(otel_enabled=True, otel_exporter_endpoint=endpoint)
    provider = setup_tracing(settings)

    assert provider is not None
    assert [type(exporter) for exporter in exporters] == [exporter_type]
    provider.shutdown()


def test_http_requests_are_traced_except_noisy_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("myna.main.setup_tracing", lambda settings: provider)

    with TestClient(create_app()) as client:
        client.get("/api/health")
        client.get("/metrics")
        client.get("/api/admin/tools")

    names = {span.name for span in exporter.get_finished_spans()}
    assert "GET /api/admin/tools" in names
    assert not any("/api/health" in name or "/metrics" in name for name in names)

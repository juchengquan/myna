"""Observability for MCP operations.

Three surfaces, kept symmetric across all MCP primitives (tools,
resources, prompts):

1. Prometheus metrics — one counter + one latency histogram per kind,
   labelled by the primitive's name, the caller, and status. Exposed at
   `/metrics`.
2. Structured audit log — one event per operation
   (`tool_call` / `resource_read` / `prompt_get`) with caller, target
   name, status, duration, and (for tools) an args fingerprint. Emitted
   via structlog so it lands in the JSON log stream. The `trace_id` and
   `span_id` of the active OTel span are injected automatically by the
   structlog `inject_trace_context` processor, so audit lines correlate
   to spans without any per-call boilerplate.
3. OTel spans — one nested span per operation, named
   `mcp.<kind>.<op> <target>`. The outer HTTP span (from FastAPI
   auto-instrumentation) becomes the parent automatically when tracing
   is enabled.

Tools are instrumented by replacing `mcp._tool_manager.call_tool`, the
single choke point for tool execution. Resources and prompts have
handlers that are bound on the lowlevel MCP server at FastMCP
construction time, so monkey-patching the FastMCP method on the
instance is too late — instead we re-register wrapped handlers on the
lowlevel server, which overwrites the original entries in
`_mcp_server.request_handlers`.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.tool_manager import ToolManager
from mcp.types import GetPromptResult
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from myna.context import current_caller
from myna.logging_config import get_logger

_TRACER = trace.get_tracer("myna.mcp")

TOOL_CALLS = Counter(
    "myna_tool_calls_total",
    "Number of MCP tool calls, by tool / caller / status.",
    labelnames=("tool", "caller", "status"),
)
TOOL_DURATION = Histogram(
    "myna_tool_call_duration_seconds",
    "MCP tool call latency in seconds, by tool.",
    labelnames=("tool",),
)

RESOURCE_READS = Counter(
    "myna_resource_reads_total",
    "Number of MCP resource reads, by URI / caller / status.",
    labelnames=("uri", "caller", "status"),
)
RESOURCE_DURATION = Histogram(
    "myna_resource_read_duration_seconds",
    "MCP resource read latency in seconds, by URI.",
    labelnames=("uri",),
)

PROMPT_GETS = Counter(
    "myna_prompt_gets_total",
    "Number of MCP prompt fetches, by name / caller / status.",
    labelnames=("name", "caller", "status"),
)
PROMPT_DURATION = Histogram(
    "myna_prompt_get_duration_seconds",
    "MCP prompt fetch latency in seconds, by name.",
    labelnames=("name",),
)

RATE_LIMIT_HITS = Counter(
    "myna_rate_limit_hits_total",
    "Number of MCP requests rejected by the rate limiter, by key kind.",
    labelnames=("key_kind",),
)

_log = get_logger("myna.audit")


def instrument(mcp: FastMCP) -> None:
    """Install metrics, audit log, and tracing for all three MCP primitives."""
    _instrument_tools(mcp)
    _instrument_resources(mcp)
    _instrument_prompts(mcp)


def _instrument_tools(mcp: FastMCP) -> None:
    tool_manager: ToolManager = mcp._tool_manager
    original = tool_manager.call_tool

    async def call_tool(
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> Any:
        caller = current_caller.get()
        args_fp = _fingerprint(arguments)
        with _TRACER.start_as_current_span(
            f"mcp.tool.call {name}",
            attributes={
                "mcp.tool.name": name,
                "mcp.caller": caller,
                "mcp.args_fingerprint": args_fp,
            },
        ) as span:
            start = time.monotonic()
            status = "ok"
            try:
                return await original(
                    name,
                    arguments,
                    context=context,
                    convert_result=convert_result,
                )
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                duration = time.monotonic() - start
                TOOL_DURATION.labels(tool=name).observe(duration)
                TOOL_CALLS.labels(tool=name, caller=caller, status=status).inc()
                span.set_attribute("mcp.status", status)
                span.set_attribute("mcp.duration_ms", round(duration * 1000, 2))
                _log.info(
                    "tool_call",
                    tool=name,
                    caller=caller,
                    status=status,
                    duration_ms=round(duration * 1000, 2),
                    args_fingerprint=args_fp,
                )

    tool_manager.call_tool = call_tool  # type: ignore[method-assign]


def _instrument_resources(mcp: FastMCP) -> None:
    # Two call paths need wrapping:
    #   1. In-process: `mcp.read_resource(uri)` (used by tests).
    #   2. Protocol: lowlevel `_mcp_server.request_handlers[ReadResourceRequest]`
    #      was bound to the unwrapped `mcp.read_resource` at FastMCP
    #      __init__ time, so monkey-patching the instance alone doesn't
    #      reroute live requests.
    # We make one wrapper and install it both places.
    original = mcp.read_resource

    async def observed_read_resource(uri: Any) -> Iterable[Any]:
        uri_str = str(uri)
        caller = current_caller.get()
        with _TRACER.start_as_current_span(
            f"mcp.resource.read {uri_str}",
            attributes={"mcp.resource.uri": uri_str, "mcp.caller": caller},
        ) as span:
            start = time.monotonic()
            status = "ok"
            try:
                return await original(uri)
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                duration = time.monotonic() - start
                RESOURCE_DURATION.labels(uri=uri_str).observe(duration)
                RESOURCE_READS.labels(uri=uri_str, caller=caller, status=status).inc()
                span.set_attribute("mcp.status", status)
                span.set_attribute("mcp.duration_ms", round(duration * 1000, 2))
                _log.info(
                    "resource_read",
                    uri=uri_str,
                    caller=caller,
                    status=status,
                    duration_ms=round(duration * 1000, 2),
                )

    mcp.read_resource = observed_read_resource  # type: ignore[method-assign]
    mcp._mcp_server.read_resource()(observed_read_resource)  # type: ignore[no-untyped-call]


def _instrument_prompts(mcp: FastMCP) -> None:
    original = mcp.get_prompt

    async def observed_get_prompt(
        name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        caller = current_caller.get()
        args_fp = _fingerprint(arguments or {})
        with _TRACER.start_as_current_span(
            f"mcp.prompt.get {name}",
            attributes={
                "mcp.prompt.name": name,
                "mcp.caller": caller,
                "mcp.args_fingerprint": args_fp,
            },
        ) as span:
            start = time.monotonic()
            status = "ok"
            try:
                return await original(name, arguments)
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                duration = time.monotonic() - start
                PROMPT_DURATION.labels(name=name).observe(duration)
                PROMPT_GETS.labels(name=name, caller=caller, status=status).inc()
                span.set_attribute("mcp.status", status)
                span.set_attribute("mcp.duration_ms", round(duration * 1000, 2))
                _log.info(
                    "prompt_get",
                    name=name,
                    caller=caller,
                    status=status,
                    duration_ms=round(duration * 1000, 2),
                    args_fingerprint=args_fp,
                )

    mcp.get_prompt = observed_get_prompt  # type: ignore[method-assign]
    mcp._mcp_server.get_prompt()(observed_get_prompt)  # type: ignore[no-untyped-call]


def _fingerprint(arguments: dict[str, Any]) -> str:
    """Short, stable hash of the arguments — useful for correlating calls
    without leaking PII or secrets into logs."""
    try:
        canonical = json.dumps(arguments, sort_keys=True, default=str)
    except TypeError:
        canonical = repr(arguments)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def render_metrics() -> tuple[bytes, str]:
    """Return the current Prometheus exposition payload + content-type."""
    return generate_latest(), CONTENT_TYPE_LATEST

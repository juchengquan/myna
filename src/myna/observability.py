"""Observability for MCP operations.

Three surfaces, kept symmetric across all MCP primitives (tools,
resources, prompts):

1. Prometheus metrics — one counter + one latency histogram per kind,
   labelled by the primitive's name, the caller, and status. Exposed at
   `/metrics`.
2. Structured audit log — one event per operation
   (`tool_call` / `resource_read` / `prompt_get`) with caller, target
   name, status, duration, and (for tools / prompts) an args fingerprint.
   Emitted via structlog so it lands in the JSON log stream. The
   `trace_id` and `span_id` of the active OTel span are injected
   automatically by the structlog `inject_trace_context` processor, so
   audit lines correlate to spans without any per-call boilerplate.
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

The three wrappers themselves are deliberately tiny: each one calls
into the shared `_observe(...)` async context manager that owns the
span / metrics / audit-log lifecycle. Adding a new MCP primitive in
future means adding a new `_OpSpec` and a thin wrapper, not a fresh
copy of the timing-and-recording boilerplate.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
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

TOOL_CACHE = Counter(
    "myna_tool_cache_total",
    "Tool result cache outcomes, by tool / outcome (hit | miss).",
    labelnames=("tool", "outcome"),
)

_log = get_logger("myna.audit")


@dataclass(frozen=True)
class _OpSpec:
    """Per-primitive observability config consumed by `_observe`.

    Centralising this lets the three wrappers (tools / resources /
    prompts) share one implementation that owns the span, metrics, and
    audit-log lifecycle.
    """

    kind: str  # "tool" | "resource" | "prompt"
    op: str  # "call" | "read" | "get"
    target_label: str  # Prometheus + audit-log key for the target's name
    target_attr: str  # OTel span attribute key for the target's name
    audit_event: str  # structlog `event` field
    counter: Counter
    duration: Histogram


_TOOL_SPEC = _OpSpec(
    kind="tool",
    op="call",
    target_label="tool",
    target_attr="mcp.tool.name",
    audit_event="tool_call",
    counter=TOOL_CALLS,
    duration=TOOL_DURATION,
)
_RESOURCE_SPEC = _OpSpec(
    kind="resource",
    op="read",
    target_label="uri",
    target_attr="mcp.resource.uri",
    audit_event="resource_read",
    counter=RESOURCE_READS,
    duration=RESOURCE_DURATION,
)
_PROMPT_SPEC = _OpSpec(
    kind="prompt",
    op="get",
    target_label="name",
    target_attr="mcp.prompt.name",
    audit_event="prompt_get",
    counter=PROMPT_GETS,
    duration=PROMPT_DURATION,
)


@contextlib.asynccontextmanager
async def _observe(
    spec: _OpSpec, target: str, *, args_fingerprint: str | None = None
) -> AsyncIterator[None]:
    """Open a span, time the body, and record metrics + audit log on exit.

    Used by the three primitive wrappers below. Callers `return` the
    upstream result from inside the `async with`; the context manager's
    `finally` block runs after the return value has propagated.
    """
    caller = current_caller.get()
    span_attrs: dict[str, Any] = {
        spec.target_attr: target,
        "mcp.caller": caller,
    }
    if args_fingerprint is not None:
        span_attrs["mcp.args_fingerprint"] = args_fingerprint

    with _TRACER.start_as_current_span(
        f"mcp.{spec.kind}.{spec.op} {target}",
        attributes=span_attrs,
    ) as span:
        start = time.monotonic()
        status = "ok"
        try:
            yield
        except Exception as exc:
            status = "error"
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        finally:
            duration = time.monotonic() - start
            duration_ms = round(duration * 1000, 2)
            spec.duration.labels(**{spec.target_label: target}).observe(duration)
            spec.counter.labels(
                **{spec.target_label: target, "caller": caller, "status": status}
            ).inc()
            span.set_attribute("mcp.status", status)
            span.set_attribute("mcp.duration_ms", duration_ms)

            audit_fields: dict[str, Any] = {
                spec.target_label: target,
                "caller": caller,
                "status": status,
                "duration_ms": duration_ms,
            }
            if args_fingerprint is not None:
                audit_fields["args_fingerprint"] = args_fingerprint
            _log.info(spec.audit_event, **audit_fields)


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
        async with _observe(_TOOL_SPEC, name, args_fingerprint=_fingerprint(arguments)):
            return await original(
                name, arguments, context=context, convert_result=convert_result
            )

    tool_manager.call_tool = call_tool  # type: ignore[method-assign]


def _instrument_resources(mcp: FastMCP) -> None:
    # Two call paths need wrapping:
    #   1. In-process: `mcp.read_resource(uri)` (used by tests).
    #   2. Protocol: lowlevel `_mcp_server.request_handlers[ReadResourceRequest]`
    #      was bound to the unwrapped `mcp.read_resource` at FastMCP
    #      __init__ time, so monkey-patching the instance alone doesn't
    #      reroute live requests.
    original = mcp.read_resource

    async def observed_read_resource(uri: Any) -> Iterable[Any]:
        async with _observe(_RESOURCE_SPEC, str(uri)):
            return await original(uri)

    mcp.read_resource = observed_read_resource  # type: ignore[method-assign]
    mcp._mcp_server.read_resource()(observed_read_resource)  # type: ignore[no-untyped-call]


def _instrument_prompts(mcp: FastMCP) -> None:
    original = mcp.get_prompt

    async def observed_get_prompt(
        name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        async with _observe(
            _PROMPT_SPEC, name, args_fingerprint=_fingerprint(arguments or {})
        ):
            return await original(name, arguments)

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


# ---------------------------------------------------------------------------
# Test helpers.
#
# Prometheus' `Counter` and `Histogram` don't ship a public read API for
# the current value of a labelled child — tests had been reaching into
# the private `_value` / `_buckets` slots. Wrapping that here gives tests
# a single, documented surface and means any future prometheus_client
# upgrade only needs a fix in one place.
# ---------------------------------------------------------------------------


def metric_value(metric: Counter, **labels: str) -> float:
    """Read the current value of a labelled counter."""
    return float(metric.labels(**labels)._value.get())


def histogram_count(metric: Histogram, **labels: str) -> float:
    """Read the total observation count of a labelled histogram."""
    buckets = metric.labels(**labels)._buckets
    return float(sum(b.get() for b in buckets))

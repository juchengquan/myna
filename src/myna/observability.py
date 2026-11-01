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

`ObservedMCPServer` overrides the server's public `call_tool`,
`read_resource`, and `get_prompt`. The protocol handlers call those same
methods, so in-process calls and live requests are observed alike. On a
2026-07-28 connection, a tool that asks the client a question returns the
question and is called again with the answer. The first round records the
status `input_required`, so `ok` still counts completed calls.

The three wrappers themselves are deliberately tiny: each one calls
into the shared `_observe(...)` async context manager that owns the
span / metrics / audit-log lifecycle. Adding a new MCP primitive in
future means adding a new `_OpSpec` and a thin wrapper, not a fresh
copy of the timing-and-recording boilerplate.

Clients choose the names and URIs they send, so metric labels are limited
to what is registered: a resource template rather than the URI that
matched it, and `unknown` for anything unregistered. Spans and audit lines
keep the raw value.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.uri_template import UriTemplate
from mcp.types import InputRequiredResult
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

_UNKNOWN_TARGET = "unknown"
_STATUS_INPUT_REQUIRED = "input_required"

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


@dataclass
class _Outcome:
    """Lets the body of `_observe` report a status other than ok or error."""

    status: str = "ok"

    def note(self, result: Any) -> Any:
        if isinstance(result, InputRequiredResult):
            self.status = _STATUS_INPUT_REQUIRED
        return result


@contextlib.asynccontextmanager
async def _observe(
    spec: _OpSpec, target: str, metric_target: str, *, args_fingerprint: str | None = None
) -> AsyncIterator[_Outcome]:
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
        outcome = _Outcome()
        try:
            yield outcome
        except Exception as exc:
            outcome.status = "error"
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        finally:
            status = outcome.status
            duration = time.monotonic() - start
            duration_ms = round(duration * 1000, 2)
            spec.duration.labels(**{spec.target_label: metric_target}).observe(duration)
            spec.counter.labels(
                **{spec.target_label: metric_target, "caller": caller, "status": status}
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


class ObservedMCPServer(MCPServer):
    """MCP server that records metrics, audit logs, and spans for each operation."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> Any:
        registered = {tool.name for tool in await self.list_tools()}
        metric_target = name if name in registered else _UNKNOWN_TARGET
        async with _observe(
            _TOOL_SPEC, name, metric_target, args_fingerprint=_fingerprint(arguments)
        ) as outcome:
            return outcome.note(await super().call_tool(name, arguments, context))

    async def read_resource(self, uri: Any, context: Context | None = None) -> Any:
        target = str(uri)
        metric_target = await self._resource_metric_target(target)
        async with _observe(_RESOURCE_SPEC, target, metric_target) as outcome:
            return outcome.note(await super().read_resource(uri, context))

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        context: Context | None = None,
    ) -> Any:
        registered = {prompt.name for prompt in await self.list_prompts()}
        metric_target = name if name in registered else _UNKNOWN_TARGET
        async with _observe(
            _PROMPT_SPEC, name, metric_target, args_fingerprint=_fingerprint(arguments or {})
        ) as outcome:
            return outcome.note(await super().get_prompt(name, arguments, context))

    async def _resource_metric_target(self, uri: str) -> str:
        if any(str(resource.uri) == uri for resource in await self.list_resources()):
            return uri
        for template in await self.list_resource_templates():
            if UriTemplate.parse(template.uri_template).match(uri) is not None:
                return template.uri_template
        return _UNKNOWN_TARGET


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

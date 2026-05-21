"""OpenTelemetry tracing setup.

Tracing is opt-in via `MYNA_OTEL_ENABLED`. When disabled (default), the
OTel SDK's no-op tracer is used everywhere — `trace.get_tracer(...)` calls
in `observability.py` become free and zero spans are produced.

When enabled, this module wires up:
- A `TracerProvider` with `service.name`, `service.version`, and
  `deployment.environment` resource attributes.
- A console span exporter, if no OTLP endpoint is configured (handy for
  local poking).
- An OTLP/HTTP exporter pointed at `MYNA_OTEL_EXPORTER_ENDPOINT` when set
  (e.g. `http://otel-collector:4318/v1/traces`).
- FastAPI auto-instrumentation, so each HTTP request gets a span.

Manual spans for MCP tool calls are emitted from `observability.instrument`.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from myna import __version__
from myna.config import Settings


def setup_tracing(settings: Settings) -> TracerProvider | None:
    """Configure and install a global `TracerProvider`. No-op when disabled.

    Returns the installed provider, or `None` when tracing is disabled.
    Idempotent — repeated calls overwrite the provider, which is the
    behavior we want for `create_app()` being called more than once
    in tests.
    """
    if not settings.otel_enabled:
        return None

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": __version__,
            "deployment.environment": settings.env,
        }
    )
    provider = TracerProvider(resource=resource)

    if settings.otel_exporter_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint))
        )
    else:
        # Useful default when someone flips the toggle locally without
        # standing up a collector.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    return provider


def install_test_exporter(exporter: SpanExporter) -> TracerProvider:
    """Wire `exporter` into the global TracerProvider for tests.

    OpenTelemetry forbids replacing the global TracerProvider once it
    has been set, so on first call we install a fresh provider and on
    subsequent calls we add a new span processor to the existing one.
    Either way every test gets its own `InMemorySpanExporter` to assert
    against — without conflicting with other tests in the run.
    """
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        current.add_span_processor(SimpleSpanProcessor(exporter))
        return current
    provider = TracerProvider(resource=Resource.create({"service.name": "myna-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider

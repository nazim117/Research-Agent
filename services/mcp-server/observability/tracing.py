"""OpenTelemetry tracing setup for mcp-server.

The mcp-server sits between chat-agent (Python) and vendor APIs (e.g. a
search backend). Every tool call originates from a chat-agent /chat request.
By extracting the W3C traceparent header chat-agent's httpx instrumentation
injects, spans started here attach to the parent trace — one Jaeger waterfall
shows the full journey from user message to vendor API and back.

Enable by setting OTEL_EXPORTER_OTLP_ENDPOINT (e.g. http://jaeger:4318, or
localhost:4318 outside Docker). Unset means this module is a no-op, keeping
the stdio transport and unit tests collector-free.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def init_tracer() -> Callable[[], None]:
    """Configure the global TracerProvider and W3C propagator.

    Call once at startup, HTTP mode only (skip for TRANSPORT=stdio — no HTTP
    layer, and the parent process manages the child's lifetime). Returns a
    shutdown callable to flush pending spans before the process exits; a
    no-op if OTEL_EXPORTER_OTLP_ENDPOINT is unset.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return lambda: None

    service_name = os.environ.get("OTEL_SERVICE_NAME", "mcp-server")
    resource = Resource.create({SERVICE_NAME: service_name})
    # No endpoint kwarg: the exporter reads OTEL_EXPORTER_OTLP_ENDPOINT itself
    # and appends /v1/traces — matching Go's otlptracehttp.WithEndpoint behavior.
    exporter = OTLPSpanExporter()

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # W3C TraceContext is the wire format chat-agent's httpx instrumentation
    # injects. Both sides must use the same propagator for spans to link.
    propagate.set_global_textmap(TraceContextTextMapPropagator())

    def shutdown() -> None:
        provider.shutdown()

    return shutdown


__all__ = ["init_tracer"]

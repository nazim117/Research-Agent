# observability.py — OpenTelemetry tracing + structured JSON logging bootstrap.
#
# Single responsibility: wire up all telemetry so main.py stays clean.
# Called once from the FastAPI lifespan hook (before the server accepts requests)
# via setup_telemetry(app).
#
# What this module sets up:
#
#   1. TracerProvider — an OTEL SDK provider backed by a BatchSpanProcessor that
#      exports spans over OTLP/HTTP to Jaeger (or any OTLP-compatible collector).
#      If OTEL is disabled or the endpoint is unreachable the provider silently
#      degrades — spans are dropped, the service keeps running.
#
#   2. FastAPIInstrumentor — auto-creates a span for every FastAPI route.
#      The span name matches the HTTP method + route template (e.g. "POST /chat").
#
#   3. HTTPXClientInstrumentor — auto-instruments every httpx.AsyncClient call,
#      injecting a traceparent header into the outbound request.  This is the
#      mechanism that carries trace context across the Python→Go boundary so that
#      mcp-server spans attach to the same trace as the /chat request.
#
#   4. Structured JSON logging — replaces the plain-text handlers on uvicorn's
#      loggers with a JSON formatter that injects trace_id, span_id, and
#      request_id into every log record.  All existing getLogger("uvicorn.error")
#      call-sites in the service automatically pick this up with no code changes.
#
# Why W3C TraceContext?
#   The Go mcp-server is instrumented with otelhttp which reads/writes the
#   standard traceparent / tracestate headers (W3C TraceContext spec).  Using the
#   same propagator on both sides ensures spans link correctly in Jaeger.

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

if TYPE_CHECKING:
    from fastapi import FastAPI

from config import settings
from request_context import get_request_id


# ─── Logging ──────────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Injects three correlation fields into every record:
      request_id — from the request-id contextvar (set by RequestIdMiddleware)
      trace_id   — hex string of the current OTEL trace, or "" when no span
      span_id    — hex string of the current OTEL span, or ""

    These three values let you pivot from a log line straight to its trace in
    Jaeger: copy the trace_id, paste it into the Jaeger search bar.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Read correlation ids from the live OTEL span context.
        span = trace.get_current_span()
        ctx = span.get_span_context()

        doc: dict = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }

        if ctx.is_valid:
            # Format as lower-case 32-char / 16-char hex strings (Jaeger convention).
            doc["trace_id"] = format(ctx.trace_id, "032x")
            doc["span_id"] = format(ctx.span_id, "016x")

        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)

        return json.dumps(doc)


def _configure_logging(log_level: str, json_mode: bool) -> None:
    """Replace uvicorn logger handlers to emit structured JSON (or leave as-is).

    All service code uses getLogger("uvicorn.error"), so configuring that logger
    (and its parent "uvicorn") is sufficient.  We do NOT configure the root logger
    because uvicorn loggers have propagate=False by default — they never reach root.
    """
    if not json_mode:
        # In plain-text mode (LOG_JSON=false, useful for local interactive dev),
        # just apply the log level without changing the format.
        logging.getLogger("uvicorn").setLevel(log_level.upper())
        return

    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lgr = logging.getLogger(name)
        # Replace existing handlers with our JSON handler.
        lgr.handlers = [handler]
        lgr.setLevel(log_level.upper())
        # Keep propagate=False (uvicorn default) to avoid duplicate output.
        lgr.propagate = False


# ─── Tracing ──────────────────────────────────────────────────────────────────

def _build_tracer_provider() -> TracerProvider | None:
    """Create and register the global TracerProvider.

    Returns None when OTEL is disabled so callers can skip instrumentation.
    """
    if not settings.otel_enabled:
        return None

    resource = Resource(attributes={SERVICE_NAME: settings.otel_service_name})

    exporter = OTLPSpanExporter(
        endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces",
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # Register as the global provider so trace.get_tracer() and auto-instrumentation
    # pick it up without needing a reference to this object.
    trace.set_tracer_provider(provider)

    # W3C TraceContext is the wire format shared with the Go mcp-server.
    # Setting it globally means all OTEL libraries (httpx instrumentor, etc.)
    # inject/extract this header automatically.
    set_global_textmap(TraceContextTextMapPropagator())

    return provider


# ─── Public API ───────────────────────────────────────────────────────────────

def setup_telemetry() -> None:
    """Bootstrap logging + tracer provider.

    Call at module level in main.py (before app = FastAPI(...)) so the provider
    is live before instrument_app() and before any requests are accepted.
    Does NOT touch the FastAPI app — call instrument_app(app) separately.
    """
    _configure_logging(settings.log_level, settings.log_json)
    _build_tracer_provider()

    # Auto-instrument httpx: every outbound HTTP call gets a span and the
    # traceparent header is injected.  This single call covers:
    #   - llm.py         (DeepSeek / Ollama calls via httpx.AsyncClient)
    #   - mcp_client.py  (PM tool calls via httpx.AsyncClient → mcp-server)
    #   - extractors.py  (URL fetching via httpx.Client)
    if settings.otel_enabled:
        HTTPXClientInstrumentor().instrument()


def instrument_app(app: FastAPI) -> None:
    """Wire FastAPIInstrumentor into the app.

    Must be called at module level, right after app = FastAPI(...) and BEFORE
    any app.add_middleware() calls.  Starlette raises RuntimeError if middleware
    is added after the app has started.
    """
    if not settings.otel_enabled:
        return
    # Auto-instrument FastAPI: every route gets a span named after its HTTP method
    # and path template (e.g. "POST /chat").
    FastAPIInstrumentor.instrument_app(app)

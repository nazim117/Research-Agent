// Package observability sets up OpenTelemetry tracing for the mcp-server.
//
// Why tracing here?
//   The mcp-server sits between the chat-agent (Python) and vendor APIs
//   (e.g. a search backend).  Every /tools/call request originates from a chat-agent
//   /chat request.  By extracting the W3C traceparent header that the chat-agent
//   injects, the mcp-server spans attach to the parent trace — so one Jaeger
//   waterfall view shows the full journey from user message to vendor API and back.
//
// How to enable:
//   Set OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 (or localhost:4318 when
//   running outside Docker).  If the env var is unset this function is a no-op,
//   keeping the stdio transport mode and unit tests collector-free.
package observability

import (
	"context"
	"os"
	"strings"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
)

// InitTracer configures the global OTEL TracerProvider and W3C propagator.
//
// Call this once at startup (HTTP mode only — skip in the TRANSPORT=stdio branch).
// The returned shutdown function must be deferred by the caller so pending spans
// are flushed before the process exits.
//
// If OTEL_EXPORTER_OTLP_ENDPOINT is empty this function is a no-op and the
// returned shutdown is a harmless identity function.
func InitTracer(ctx context.Context) (shutdown func(context.Context) error, err error) {
	noop := func(context.Context) error { return nil }

	endpoint := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
	if endpoint == "" {
		// No collector configured — leave the global provider as the default
		// no-op so all otel.Tracer(...).Start(...) calls compile and run safely.
		return noop, nil
	}

	svcName := os.Getenv("OTEL_SERVICE_NAME")
	if svcName == "" {
		svcName = "mcp-server"
	}

	// otlptracehttp.WithEndpoint expects host:port (no scheme).
	// Strip http:// or https:// if the user provided the full URL.
	host := strings.TrimPrefix(endpoint, "https://")
	host = strings.TrimPrefix(host, "http://")

	exp, err := otlptracehttp.New(ctx,
		otlptracehttp.WithEndpoint(host),
		// Jaeger all-in-one listens on plain HTTP — no TLS needed locally.
		otlptracehttp.WithInsecure(),
	)
	if err != nil {
		return noop, err
	}

	res, err := resource.New(ctx,
		resource.WithAttributes(semconv.ServiceName(svcName)),
	)
	if err != nil {
		return noop, err
	}

	tp := sdktrace.NewTracerProvider(
		// BatchSpanProcessor ships spans asynchronously; the process keeps serving
		// requests while spans are being exported in the background.
		sdktrace.WithBatcher(exp),
		sdktrace.WithResource(res),
	)

	// Register as the global provider so otelhttp and manual Tracer.Start calls
	// in the handler all share the same provider without needing a reference.
	otel.SetTracerProvider(tp)

	// W3C TraceContext is the wire format that chat-agent's httpx instrumentation
	// injects.  Both sides must use the same propagator for spans to link.
	otel.SetTextMapPropagator(propagation.TraceContext{})

	return tp.Shutdown, nil
}

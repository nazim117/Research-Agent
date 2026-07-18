package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/joho/godotenv"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"mcp-server/internal/mcp"
	"mcp-server/internal/observability"
	"mcp-server/internal/tools"
)

// findEnvFile walks up from the working directory looking for a .env file.
func findEnvFile() string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}
	for {
		candidate := filepath.Join(dir, ".env")
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func main() {
	log.SetOutput(os.Stderr)
	log.SetPrefix("[mcp] ")

	// Load .env by walking up from cwd until we find it.
	if envFile := findEnvFile(); envFile != "" {
		if err := godotenv.Load(envFile); err != nil {
			log.Printf("failed to load %s: %v", envFile, err)
		} else {
			log.Printf("loaded env from %s", envFile)
		}
	}

	registry := tools.NewRegistry()

	// stdio transport: used when launched as a child process by an MCP client
	// (e.g. Claude Code, Claude Desktop). Set TRANSPORT=stdio to enable.
	// We skip tracing in stdio mode — there is no HTTP layer, and the parent
	// process manages the lifetime of the child.
	if os.Getenv("TRANSPORT") == "stdio" {
		server := mcp.NewServer(registry)
		if err := server.Serve(os.Stdin, os.Stdout); err != nil {
			log.Fatal(err)
		}
		return
	}

	// ── Tracing ────────────────────────────────────────────────────────────────
	// InitTracer is a no-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset, so the
	// service starts cleanly without a collector in local non-Docker runs.
	ctx := context.Background()
	shutdown, err := observability.InitTracer(ctx)
	if err != nil {
		log.Printf("tracing init failed (continuing without traces): %v", err)
	} else {
		// Flush pending spans on shutdown so nothing is dropped on SIGTERM.
		defer func() {
			if err := shutdown(ctx); err != nil {
				log.Printf("tracer shutdown error: %v", err)
			}
		}()
	}

	port := os.Getenv("PORT")
	if port == "" {
		port = "8083"
	}

	mux := http.NewServeMux()

	// GET /tools — list all available tool definitions.
	mux.HandleFunc("/tools", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(registry.Definitions())
	})

	// POST /tools/call — invoke a tool by name.
	//
	// A child span "tool.<name>" is started here so that Jaeger shows the exact
	// tool name (e.g. "tool.jira_search_issues") rather than a generic "POST".
	// The span is a child of the incoming traceparent injected by chat-agent's
	// httpx auto-instrumentation — linking this execution to the /chat trace.
	mux.HandleFunc("/tools/call", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var params mcp.ToolCallParams
		if err := json.NewDecoder(r.Body).Decode(&params); err != nil {
			http.Error(w, "invalid request body: "+err.Error(), http.StatusBadRequest)
			return
		}

		// Start a span named after the tool so the waterfall view in Jaeger
		// shows exactly which vendor API was called.
		tracer := otel.Tracer("mcp-server")
		spanCtx, span := tracer.Start(r.Context(), "tool."+params.Name)
		span.SetAttributes(attribute.String("tool.name", params.Name))
		defer span.End()

		started := time.Now()
		result, err := registry.Call(params.Name, params.Arguments)
		// Record Prometheus metrics regardless of outcome.
		// RecordToolCall captures duration and increments the calls counter.
		observability.RecordToolCall(params.Name, err, started)
		if err != nil {
			span.RecordError(err)
			span.SetStatus(codes.Error, err.Error())
			result = mcp.ToolCallResult{
				Content: []mcp.ContentBlock{{Type: "text", Text: err.Error()}},
				IsError: true,
			}
		}

		// Use spanCtx so the encode step is still within the span's context.
		_ = spanCtx

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(result)
	})

	// GET /metrics — Prometheus scrape endpoint.
	// Exposes mcp_server_tool_calls_total, mcp_server_tool_call_duration_seconds,
	// and standard Go runtime metrics (GC, goroutines, memory).
	// Prometheus scrapes this from inside the Docker network at mcp-server:8083/metrics.
	mux.Handle("/metrics", observability.Handler())

	// GET /health
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "healthy"})
	})

	// GET /integrations/status — Jira/GitHub configured state for the dashboard.
	// Never returns secrets (email, API token) — see tools.Registry.IntegrationsStatus.
	mux.HandleFunc("/integrations/status", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(registry.IntegrationsStatus())
	})

	log.Printf("MCP server listening on :%s (tools: %d)", port, len(registry.Definitions()))

	// otelhttp.NewHandler wraps the entire mux with:
	//   1. traceparent / tracestate header extraction (W3C TraceContext)
	//   2. a root span for every incoming request named "mcp-server"
	// This is what connects the Python→Go trace — the extracted context becomes
	// the parent of any spans we start inside the handlers above.
	log.Fatal(http.ListenAndServe(":"+port, otelhttp.NewHandler(corsMiddleware(mux), "mcp-server")))
}

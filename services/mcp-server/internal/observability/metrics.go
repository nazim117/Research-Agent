package observability

// metrics.go — Prometheus metrics for mcp-server tool calls.
//
// Single responsibility: define counters/histograms for PM tool invocations
// and expose a scrape handler.  HTTP-level metrics (request rate, latency) are
// handled by otelhttp's built-in prometheus integration — this file only adds
// the domain-specific "which tool was called and did it succeed" view.
//
// Why promauto?
//   promauto registers metrics with the default registry at package init time.
//   This means the metrics exist the moment the package is imported — no
//   explicit register call needed in main.go, and no risk of double-registration
//   if the package is imported more than once.
//
// Metric names follow Prometheus conventions:
//   <service>_<subsystem>_<unit>_total for counters.

import (
	"net/http"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	// toolCallsTotal counts every tool invocation by name and outcome.
	// outcome label: "ok" on success, "error" when registry.Call returns an error.
	// Use rate(mcp_server_tool_calls_total[5m]) in Grafana for call rate per tool.
	toolCallsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "mcp_server_tool_calls_total",
			Help: "Total tool invocations by name and outcome",
		},
		[]string{"tool", "outcome"},
	)

	// toolCallDurationSeconds records how long each tool call takes end-to-end.
	// This captures the latency of the underlying vendor API (e.g. web search).
	// Use histogram_quantile(0.95, rate(mcp_server_tool_call_duration_seconds_bucket[5m]))
	// in Grafana to track p95 latency per tool.
	toolCallDurationSeconds = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "mcp_server_tool_call_duration_seconds",
			Help:    "Duration of tool calls in seconds",
			Buckets: prometheus.DefBuckets, // 0.005 → 10 s, covers most API calls
		},
		[]string{"tool"},
	)
)

// RecordToolCall records a single tool invocation result.
//
// Call this immediately after registry.Call() returns, passing:
//   - name:    the tool name (e.g. "web_search")
//   - err:     nil on success, non-nil on failure
//   - started: time.Now() captured just before the registry.Call() call
func RecordToolCall(name string, err error, started time.Time) {
	outcome := "ok"
	if err != nil {
		outcome = "error"
	}
	toolCallsTotal.WithLabelValues(name, outcome).Inc()
	toolCallDurationSeconds.WithLabelValues(name).Observe(time.Since(started).Seconds())
}

// Handler returns the Prometheus HTTP scrape handler.
// Register it on the mux at /metrics in main.go.
func Handler() http.Handler {
	return promhttp.Handler()
}

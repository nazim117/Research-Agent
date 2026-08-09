"""Prometheus metrics for mcp-server tool calls.

Single responsibility: define counters/histograms for tool invocations and
expose a scrape endpoint. Metric names match the previous Go service exactly
(mcp_server_tool_calls_total, mcp_server_tool_call_duration_seconds) so
existing Grafana dashboards/alerts need no changes.
"""

from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

tool_calls_total = Counter(
    "mcp_server_tool_calls_total",
    "Total tool invocations by name and outcome",
    ["tool", "outcome"],
)

tool_call_duration_seconds = Histogram(
    "mcp_server_tool_call_duration_seconds",
    "Duration of tool calls in seconds",
    ["tool"],
)


def record_tool_call(name: str, error: BaseException | None, started: float) -> None:
    """Record one tool invocation. `started` is a time.monotonic() timestamp
    captured immediately before the call."""
    outcome = "error" if error is not None else "ok"
    tool_calls_total.labels(tool=name, outcome=outcome).inc()
    tool_call_duration_seconds.labels(tool=name).observe(time.monotonic() - started)


def render_latest() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics scrape endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST


__all__ = ["record_tool_call", "render_latest", "tool_call_duration_seconds", "tool_calls_total"]

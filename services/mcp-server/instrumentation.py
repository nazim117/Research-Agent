"""Shared tool-call instrumentation: tracing + Prometheus metrics.

Every tools/*.py module registers its functions with @tool(mcp) instead of
FastMCP's raw @mcp.tool() decorator. This is the one piece of boilerplate a
tool author opts into — it wraps the call with a "tool.<name>" OTEL span and
records Prometheus metrics, so tool implementations stay pure business logic.

functools.wraps preserves the original function's signature via __wrapped__,
which FastMCP's schema introspection (inspect.signature) follows — so the
generated JSON schema still reflects the real typed parameters, not
*args/**kwargs. Verified in services/mcp-server/tests/test_server_loader.py.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any

from opentelemetry import trace

from observability.metrics import record_tool_call

_tracer = trace.get_tracer("mcp-server")


def tool(mcp: Any) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator factory: @tool(mcp) registers fn on mcp with tracing+metrics."""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        name = fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            with _tracer.start_as_current_span(f"tool.{name}") as span:
                span.set_attribute("tool.name", name)
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    record_tool_call(name, exc, started)
                    raise
                record_tool_call(name, None, started)
                return result

        mcp.tool()(wrapper)
        return fn

    return decorator


__all__ = ["tool"]

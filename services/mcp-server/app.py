"""Composes the ASGI app served over Streamable HTTP: the MCP endpoint at
/mcp (from FastMCP) plus the admin routes (/health, /config/env, /metrics —
registered via custom_route in admin/*.py, see server.py) as siblings in the
same Starlette app FastMCP builds. CORS stays wide open at this layer — the
Chrome extension's origin changes every reload — with /config/env's
narrower origin check layered on top (admin/config_env.py)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from starlette.middleware.cors import CORSMiddleware


def build_app(mcp: FastMCP):
    app = mcp.streamable_http_app()
    app = CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["*"],
    )
    # Extracts the incoming traceparent header and starts a root span per
    # request, so mcp-server's spans attach to chat-agent's parent trace.
    app = OpenTelemetryMiddleware(app)
    return app


__all__ = ["build_app"]

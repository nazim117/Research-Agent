"""GET /metrics — Prometheus scrape endpoint."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from observability.metrics import render_latest


def register(mcp) -> None:
    @mcp.custom_route("/metrics", methods=["GET"])
    async def metrics(request: Request):
        body, content_type = render_latest()
        return Response(body, media_type=content_type)

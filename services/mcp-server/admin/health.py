"""GET /health — also reports which web_search backend is active so
chat-agent's aggregate health check can warn the dashboard before a chat-time
search silently fails."""

from __future__ import annotations

import os

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse


async def _probe_reachable(base_url: str) -> bool:
    """Cheap GET against the base URL's root — SearXNG always serves its
    search UI there, so any non-5xx response means the instance is up."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(base_url.rstrip("/") + "/")
    except httpx.HTTPError:
        return False
    return resp.status_code < 500


async def check_web_search_backend() -> dict:
    base_url = os.environ.get("SEARXNG_BASE_URL")
    if base_url:
        return {
            "backend": "searxng",
            "configured": True,
            "reachable": await _probe_reachable(base_url),
        }
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        return {"backend": "brave", "configured": True}
    return {"backend": "duckduckgo", "configured": False}


def register(mcp) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        return JSONResponse({"status": "healthy", "web_search": await check_web_search_backend()})

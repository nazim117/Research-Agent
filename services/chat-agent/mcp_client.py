# mcp_client.py — client for mcp-server.
#
# mcp-server exposes a real MCP (Model Context Protocol) endpoint at
# {base_url}/mcp, served over the Streamable HTTP transport, plus a few
# plain HTTP admin routes that are *not* part of the MCP protocol:
#   GET       /health        — liveness + web_search backend status
#   GET/PUT   /config/env    — env var admin API for the dashboard
#
# call() is a real MCP client: it opens a Streamable HTTP session, calls
# tools/call, and unwraps the result. The admin routes stay on plain httpx
# since they aren't MCP.
#
# The mcp-server holds all vendor credentials (e.g. BRAVE_SEARCH_API_KEY).
# This client never reads or stores those values.

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class MCPError(Exception):
    """Raised when mcp-server is unreachable, returns non-2xx (admin routes),
    or a tool call fails.

    status_code mirrors the HTTP status when available (admin routes only —
    the MCP client transport doesn't expose one, so call() failures default
    to 502) because from chat-agent's perspective mcp-server is a downstream
    dependency.
    """

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class MCPClient:
    """Client for mcp-server: a real MCP client for tool calls, plain httpx
    for the admin-only routes.

    Args:
        base_url:   Base URL of the mcp-server, e.g. "http://localhost:8083".
        timeout:    Request timeout in seconds (default 30 s; some tool calls
                    may be slower than usual).
        transport:  Optional httpx transport override for the admin routes —
                    inject a fake transport in unit tests instead of hitting
                    a real network.
        session_cm: Optional factory returning an async context manager that
                    yields an initialized-or-initializable ClientSession, for
                    call(). Tests inject a fake session this way instead of
                    a real Streamable HTTP connection.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8083",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        session_cm: Callable[[], AbstractAsyncContextManager[ClientSession]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport
        self._session_cm = session_cm

    def _open_session(self) -> AbstractAsyncContextManager[ClientSession]:
        if self._session_cm is not None:
            return self._session_cm()

        @contextlib.asynccontextmanager
        async def _default():
            async with (
                streamablehttp_client(f"{self._base_url}/mcp", timeout=self._timeout) as (
                    read,
                    write,
                    _get_session_id,
                ),
                ClientSession(read, write) as session,
            ):
                yield session

        return _default()

    async def call(self, name: str, arguments: dict) -> dict:
        """Invoke a named tool on the mcp-server and return the result as a dict.

        Args:
            name:       Tool name, e.g. "web_search".
            arguments:  Tool input as a plain dict.

        Returns:
            Parsed result dict (structuredContent if present, else the JSON
            object embedded in content[0].text).

        Raises:
            MCPError: Connection/session failure, tool isError=true, or
                      non-JSON text content with no structuredContent.
        """
        try:
            async with self._open_session() as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
        except Exception as exc:
            raise MCPError(f"mcp-server unreachable: {exc}") from exc

        if result.isError:
            content = result.content or []
            text = content[0].text if content else ""
            raise MCPError(f"tool {name!r} error: {text}")

        structured: Any = getattr(result, "structuredContent", None)
        if structured is not None:
            return structured

        content = result.content or []
        if not content:
            return {}

        raw_text = content[0].text
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise MCPError(f"tool {name!r} returned non-JSON text: {raw_text[:200]}") from exc

    async def _get(self, path: str) -> dict:
        """Shared GET helper for mcp-server's plain admin HTTP endpoints
        (not the MCP protocol call() above uses)."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                resp = await client.get(f"{self._base_url}{path}")
        except httpx.RequestError as exc:
            raise MCPError(f"mcp-server unreachable: {exc}") from exc

        if resp.status_code != 200:
            raise MCPError(
                f"mcp-server returned HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        return resp.json()

    async def get_health(self) -> dict:
        """Check mcp-server's own liveness. Raises MCPError if unreachable."""
        return await self._get("/health")

    async def get_web_search_status(self) -> dict:
        """Which web-search backend mcp-server is configured to use, and (for
        SearXNG) whether it's actually reachable right now — see
        mcp-server's tools/web.py dispatch order and this endpoint's
        `web_search` field. Raises MCPError if mcp-server itself is unreachable.
        """
        health = await self._get("/health")
        return health.get("web_search", {})

    async def _put(self, path: str, json_body: dict) -> dict:
        """Shared PUT helper, mirrors _get."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                resp = await client.put(f"{self._base_url}{path}", json=json_body)
        except httpx.RequestError as exc:
            raise MCPError(f"mcp-server unreachable: {exc}") from exc

        if resp.status_code != 200:
            raise MCPError(
                f"mcp-server returned HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        return resp.json()

    async def get_env_vars(self) -> list[dict]:
        """Fetch mcp-server's own web-search env var state.

        Never includes secrets in full — see mcp-server's env_vars.get_env_vars.
        """
        return await self._get("/config/env")

    async def set_env_var(self, key: str, value: str) -> None:
        """Write one web-search env var. The value is forwarded as-is to
        mcp-server and never persisted or logged by this service —
        mcp-server is the only process that owns these credentials.
        """
        await self._put("/config/env", {"key": key, "value": value})

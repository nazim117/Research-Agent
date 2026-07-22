# mcp_client.py — thin async client for the mcp-server HTTP API.
#
# mcp-server exposes two endpoints:
#   GET  /tools          — list available tool definitions
#   POST /tools/call     — invoke a tool by name, get back a result
#
# The mcp-server holds all vendor credentials (JIRA_*, GITHUB_TOKEN).
# This client never reads or stores those values.
#
# Protocol: JSON over HTTP.
# Request:  {"name": "<tool>", "arguments": {...}}
# Response: {"content": [{"type": "text", "text": "<json-string>"}], "isError": bool}
#
# The tool result is embedded as a JSON string inside content[0].text.
# This client parses that inner JSON and returns it as a plain dict.

from __future__ import annotations

import json

import httpx


class MCPError(Exception):
    """Raised when mcp-server is unreachable, returns non-2xx, or sets isError=true.

    status_code mirrors the HTTP status when available; defaults to 502 (bad
    gateway) because from the chat-agent's perspective the mcp-server is a
    downstream dependency.
    """

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class MCPClient:
    """Async client for the mcp-server tool-call endpoint.

    Args:
        base_url:  Base URL of the mcp-server, e.g. "http://localhost:8083".
        timeout:   Request timeout in seconds (default 30 s; tool calls that
                   hydrate many Jira issues may be slower than usual).
        transport: Optional httpx transport override — inject a fake transport
                   in unit tests instead of hitting a real network.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8083",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    async def call(self, name: str, arguments: dict) -> dict:
        """Invoke a named tool on the mcp-server and return the result as a dict.

        Args:
            name:       Tool name, e.g. "jira_search_issues".
            arguments:  Tool input as a plain dict (will be JSON-encoded).

        Returns:
            Parsed result dict (the JSON object embedded in content[0].text).

        Raises:
            MCPError: Connection failure, non-2xx HTTP status, isError=true,
                      or non-JSON response text.
        """
        payload = {"name": name, "arguments": arguments}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                resp = await client.post(
                    f"{self._base_url}/tools/call",
                    json=payload,
                )
        except httpx.RequestError as exc:
            raise MCPError(f"mcp-server unreachable: {exc}") from exc

        if resp.status_code not in (200, 201):
            raise MCPError(
                f"mcp-server returned HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )

        data = resp.json()

        if data.get("isError"):
            # mcp-server sets isError=true when a tool call fails (e.g. Jira
            # returns 401, or required env vars are missing on the Go side).
            content = data.get("content") or []
            text = content[0].get("text", "") if content else ""
            raise MCPError(f"tool {name!r} error: {text}")

        content = data.get("content") or []
        if not content:
            return {}

        raw_text = content[0].get("text", "{}")
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise MCPError(f"tool {name!r} returned non-JSON text: {raw_text[:200]}") from exc

    async def _get(self, path: str) -> dict:
        """Shared GET helper for mcp-server's plain HTTP endpoints (not the
        JSON-RPC-style /tools/call protocol `call()` above uses).
        """
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
        internal/tools/web.go's dispatch order and this endpoint's
        `web_search` field. Raises MCPError if mcp-server itself is unreachable.
        """
        health = await self._get("/health")
        return health.get("web_search", {})

    async def get_integrations_status(self) -> dict:
        """Fetch Jira/GitHub configured status from mcp-server.

        Never includes secrets — mcp-server's /integrations/status endpoint
        only reports configured booleans and the non-secret Jira base URL.
        """
        return await self._get("/integrations/status")

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
        """Fetch mcp-server's own Jira/GitHub/web-search env var state.

        Never includes secrets in full — see mcp-server's Registry.EnvVars.
        """
        return await self._get("/config/env")

    async def set_env_var(self, key: str, value: str) -> None:
        """Write one Jira/GitHub/web-search env var. The value is forwarded
        as-is to mcp-server and never persisted or logged by this service —
        mcp-server is the only process that owns these credentials.
        """
        await self._put("/config/env", {"key": key, "value": value})

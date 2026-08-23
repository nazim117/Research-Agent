# tests/test_mcp_client.py — unit tests for the MCPClient.
#
# No live network. call() is exercised via a fake session_cm (a stand-in for
# a real MCP ClientSession) instead of a real Streamable HTTP connection.
# get_health/get_env_vars/set_env_var stay on plain httpx and are exercised
# via the existing `transport` seam.
#
# Concepts tested:
#   - Successful call returns parsed dict from content[0].text
#   - Successful call with structuredContent returns it directly
#   - isError=true raises MCPError
#   - Non-JSON content[0].text (and no structuredContent) raises MCPError
#   - Empty content list returns {}
#   - Session/connection failure raises MCPError with status_code=502

import contextlib
import json

import httpx
import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from mcp_client import MCPClient, MCPError

# ---------------------------------------------------------------------------
# Fake session helpers
# ---------------------------------------------------------------------------

class _FakeSession:
    """Stands in for a real mcp.ClientSession — call() only ever calls
    initialize() then call_tool()."""

    def __init__(
        self,
        result: CallToolResult | None = None,
        call_error: Exception | None = None,
        list_tools_result: ListToolsResult | None = None,
        list_tools_error: Exception | None = None,
    ) -> None:
        self._result = result
        self._call_error = call_error
        self._list_tools_result = list_tools_result
        self._list_tools_error = list_tools_error

    async def initialize(self):
        return None

    async def call_tool(self, name: str, arguments: dict) -> CallToolResult:
        if self._call_error is not None:
            raise self._call_error
        assert self._result is not None
        return self._result

    async def list_tools(self) -> ListToolsResult:
        if self._list_tools_error is not None:
            raise self._list_tools_error
        assert self._list_tools_result is not None
        return self._list_tools_result


def _session_cm(session: _FakeSession | None = None, open_error: Exception | None = None):
    """Build a session_cm factory for MCPClient — mirrors what
    streamablehttp_client + ClientSession would yield, without a real
    connection."""

    @contextlib.asynccontextmanager
    async def _cm():
        if open_error is not None:
            raise open_error
        yield session

    return _cm


def _ok_result(result: dict) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))], isError=False)


def _error_result(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], isError=True)


# ---------------------------------------------------------------------------
# call() tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("result", [
    {"total": 0, "issues": []},
    {"key": "ALPHA-1", "summary": "Fix bug"},
    {},
])
async def test_call_success_returns_parsed_dict(result):
    """Successful response → call() returns the parsed result dict."""
    client = MCPClient(session_cm=_session_cm(_FakeSession(_ok_result(result))))
    out = await client.call("web_search", {"query": "project = A"})
    assert out == result


async def test_call_prefers_structured_content_when_present():
    """A CallToolResult with structuredContent set skips text-JSON parsing."""
    result = CallToolResult(
        content=[TextContent(type="text", text="ignored")],
        structuredContent={"ok": True, "key": "a"},
        isError=False,
    )
    client = MCPClient(session_cm=_session_cm(_FakeSession(result)))
    out = await client.call("memory_set", {"key": "a", "value": "b"})
    assert out == {"ok": True, "key": "a"}


async def test_call_is_error_raises_mcp_error():
    """isError=true in the result → MCPError raised with tool name in message."""
    client = MCPClient(session_cm=_session_cm(_FakeSession(_error_result("tool is not configured"))))
    with pytest.raises(MCPError, match="tool is not configured"):
        await client.call("web_search", {"query": "project = A"})


async def test_call_non_json_text_raises_mcp_error():
    """Non-JSON content[0].text with no structuredContent → MCPError (not a crash)."""
    result = CallToolResult(content=[TextContent(type="text", text="not json at all")], isError=False)
    client = MCPClient(session_cm=_session_cm(_FakeSession(result)))
    with pytest.raises(MCPError, match="non-JSON"):
        await client.call("web_fetch", {"key": "A-1"})


async def test_call_empty_content_returns_empty_dict():
    """Empty content list → {} (not an error, some tools return nothing)."""
    result = CallToolResult(content=[], isError=False)
    client = MCPClient(session_cm=_session_cm(_FakeSession(result)))
    out = await client.call("memory_set", {"key": "x", "value": "y"})
    assert out == {}


async def test_call_session_open_failure_raises_mcp_error():
    """Session/connection failure (e.g. mcp-server down) → MCPError, status 502."""
    client = MCPClient(session_cm=_session_cm(open_error=ConnectionRefusedError("refused")))
    with pytest.raises(MCPError) as exc_info:
        await client.call("web_search", {"query": "project = A"})
    assert exc_info.value.status_code == 502
    assert "unreachable" in str(exc_info.value).lower()


async def test_call_tool_error_raises_mcp_error_with_status_502():
    """call_tool() raising mid-call also surfaces as a flat 502 — the MCP
    client transport doesn't expose a granular HTTP status the way the old
    REST client did."""
    client = MCPClient(session_cm=_session_cm(_FakeSession(call_error=RuntimeError("boom"))))
    with pytest.raises(MCPError) as exc_info:
        await client.call("web_search", {"query": "x"})
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# admin routes (get_health / get_env_vars / set_env_var) — unchanged, still
# plain httpx via the `transport` seam.
# ---------------------------------------------------------------------------

class _StaticTransport(httpx.AsyncBaseTransport):
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._response._request = request  # type: ignore[attr-defined]
        return self._response


class _ErrorTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)


async def test_get_health_returns_parsed_json():
    resp = httpx.Response(200, json={"status": "healthy", "web_search": {"backend": "duckduckgo"}})
    client = MCPClient(transport=_StaticTransport(resp))
    out = await client.get_health()
    assert out["status"] == "healthy"


async def test_get_health_unreachable_raises_mcp_error():
    client = MCPClient(transport=_ErrorTransport())
    with pytest.raises(MCPError) as exc_info:
        await client.get_health()
    assert exc_info.value.status_code == 502


async def test_get_env_vars_non_200_raises_mcp_error_with_status():
    resp = httpx.Response(500, text="internal error")
    client = MCPClient(transport=_StaticTransport(resp))
    with pytest.raises(MCPError) as exc_info:
        await client.get_env_vars()
    assert exc_info.value.status_code == 500


async def test_set_env_var_puts_key_and_value():
    resp = httpx.Response(200, json={"ok": True})
    client = MCPClient(transport=_StaticTransport(resp))
    await client.set_env_var("SEARXNG_BASE_URL", "http://x:8080")  # no exception = success


# ---------------------------------------------------------------------------
# get_tool_schemas() — used by the agentic web tool-calling loop in main.py
# to build the OpenAI `tools` payload from mcp-server's real tool schemas.
# ---------------------------------------------------------------------------

_ALL_TOOLS = ListToolsResult(
    tools=[
        Tool(
            name="web_search",
            description="Search the web.",
            inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
        ),
        Tool(
            name="web_fetch",
            description="Fetch a URL.",
            inputSchema={"type": "object", "properties": {"url": {"type": "string"}}},
        ),
        Tool(name="memory_set", description="Store a memory.", inputSchema={"type": "object"}),
    ]
)


async def test_get_tool_schemas_converts_to_openai_format():
    client = MCPClient(session_cm=_session_cm(_FakeSession(list_tools_result=_ALL_TOOLS)))
    schemas = await client.get_tool_schemas()
    assert len(schemas) == 3
    web_search = next(s for s in schemas if s["function"]["name"] == "web_search")
    assert web_search == {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }


async def test_get_tool_schemas_filters_by_names():
    client = MCPClient(session_cm=_session_cm(_FakeSession(list_tools_result=_ALL_TOOLS)))
    schemas = await client.get_tool_schemas(names=["web_search", "web_fetch"])
    assert {s["function"]["name"] for s in schemas} == {"web_search", "web_fetch"}


async def test_get_tool_schemas_session_failure_raises_mcp_error():
    client = MCPClient(session_cm=_session_cm(open_error=ConnectionRefusedError("refused")))
    with pytest.raises(MCPError) as exc_info:
        await client.get_tool_schemas()
    assert "unreachable" in str(exc_info.value).lower()


pytestmark = pytest.mark.asyncio

# tests/test_mcp_client.py — unit tests for the MCPClient.
#
# No live network.  Responses are faked via httpx.AsyncBaseTransport.
#
# Concepts tested:
#   - Successful call returns parsed dict from content[0].text
#   - isError=true raises MCPError
#   - Non-2xx HTTP status raises MCPError
#   - Non-JSON content[0].text raises MCPError
#   - Empty content list returns {}
#   - httpx.RequestError (connection refused) raises MCPError
#   - Every call() invocation is logged to the toolbox, on both the success
#     and failure paths

import json

import httpx
import pytest

from mcp_client import MCPClient, MCPError
from toolbox import ToolboxStore

# ---------------------------------------------------------------------------
# Fake transport helpers
# ---------------------------------------------------------------------------

class _StaticTransport(httpx.AsyncBaseTransport):
    """Return a single pre-built response for every request."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._response._request = request  # type: ignore[attr-defined]
        return self._response


class _ErrorTransport(httpx.AsyncBaseTransport):
    """Raise httpx.ConnectError for every request."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)


def _ok_response(result: dict) -> httpx.Response:
    """Build a successful mcp-server response wrapping `result` as JSON text."""
    body = {
        "content": [{"type": "text", "text": json.dumps(result)}],
        "isError": False,
    }
    return httpx.Response(200, json=body)


def _error_response(message: str) -> httpx.Response:
    """Build an mcp-server isError=true response."""
    body = {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }
    return httpx.Response(200, json=body)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("result", [
    {"total": 0, "issues": []},
    {"key": "ALPHA-1", "summary": "Fix bug"},
    {},
])
async def test_call_success_returns_parsed_dict(result):
    """Successful response → call() returns the parsed result dict."""
    client = MCPClient(transport=_StaticTransport(_ok_response(result)))
    out = await client.call("web_search", {"query": "project = A"}, project_id="proj-1")
    assert out == result


async def test_call_is_error_raises_mcp_error():
    """isError=true in the response body → MCPError raised with tool name in message."""
    client = MCPClient(transport=_StaticTransport(_error_response("tool is not configured")))
    with pytest.raises(MCPError, match="tool is not configured"):
        await client.call("web_search", {"query": "project = A"}, project_id="proj-1")


@pytest.mark.parametrize("status_code", [400, 401, 500, 503])
async def test_call_non_2xx_raises_mcp_error(status_code):
    """Non-2xx HTTP status → MCPError with that status_code."""
    resp = httpx.Response(status_code, text="error body")
    client = MCPClient(transport=_StaticTransport(resp))
    with pytest.raises(MCPError) as exc_info:
        await client.call("web_fetch", {"key": "A-1"}, project_id="proj-1")
    assert exc_info.value.status_code == status_code


async def test_call_non_json_text_raises_mcp_error():
    """Non-JSON content[0].text → MCPError (not a crash / KeyError)."""
    body = {"content": [{"type": "text", "text": "not json at all"}], "isError": False}
    resp = httpx.Response(200, json=body)
    client = MCPClient(transport=_StaticTransport(resp))
    with pytest.raises(MCPError, match="non-JSON"):
        await client.call("web_fetch", {"key": "A-1"}, project_id="proj-1")


async def test_call_empty_content_returns_empty_dict():
    """Empty content list → {} (not an error, some tools return nothing)."""
    body = {"content": [], "isError": False}
    resp = httpx.Response(200, json=body)
    client = MCPClient(transport=_StaticTransport(resp))
    out = await client.call("memory_set", {"key": "x", "value": "y"}, project_id="proj-1")
    assert out == {}


async def test_call_connection_error_raises_mcp_error():
    """httpx.RequestError (e.g. mcp-server down) → MCPError with status 502."""
    client = MCPClient(transport=_ErrorTransport())
    with pytest.raises(MCPError) as exc_info:
        await client.call("web_search", {"query": "project = A"}, project_id="proj-1")
    assert exc_info.value.status_code == 502
    assert "unreachable" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Toolbox logging
# ---------------------------------------------------------------------------

async def test_call_success_logs_to_toolbox(tmp_path):
    toolbox = ToolboxStore(str(tmp_path / "test.db"))
    await toolbox.init()
    client = MCPClient(
        transport=_StaticTransport(_ok_response({"results": [1, 2]})),
        toolbox=toolbox,
    )
    await client.call("web_search", {"query": "x"}, project_id="proj-1")

    rows = await toolbox.list_by_project("proj-1")
    assert len(rows) == 1
    assert rows[0].tool_name == "web_search"
    assert rows[0].success is True
    assert rows[0].error is None
    assert rows[0].result_summary is not None
    assert rows[0].duration_ms is not None


async def test_call_failure_logs_to_toolbox(tmp_path):
    toolbox = ToolboxStore(str(tmp_path / "test.db"))
    await toolbox.init()
    client = MCPClient(transport=_ErrorTransport(), toolbox=toolbox)

    with pytest.raises(MCPError):
        await client.call("web_search", {"query": "x"}, project_id="proj-1")

    rows = await toolbox.list_by_project("proj-1")
    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].error is not None
    assert rows[0].result_summary is None


async def test_call_without_toolbox_does_not_raise():
    """MCPClient stays usable with no toolbox configured (the default)."""
    client = MCPClient(transport=_StaticTransport(_ok_response({"ok": True})))
    out = await client.call("web_search", {"query": "x"}, project_id="proj-1")
    assert out == {"ok": True}


pytestmark = pytest.mark.asyncio

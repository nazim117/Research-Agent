"""Drives the real tool registry through an actual MCP client/server session
(in-memory transport, no socket) rather than calling tool functions
directly — the Python equivalent of the old Go service's
internal/mcp_test/server_test.go coverage."""

from __future__ import annotations

import json

from mcp.shared.memory import create_connected_server_and_client_session

from server import build_mcp


async def test_tools_list_returns_all_nine_tools():
    mcp = build_mcp()
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.list_tools()
        names = {t.name for t in result.tools}
        assert names == {
            "memory_set",
            "memory_get",
            "memory_list",
            "web_search",
            "web_fetch",
            "file_read",
            "file_write",
            "file_list",
            "http_request",
        }


async def test_memory_set_and_get_roundtrip_over_real_session():
    mcp = build_mcp()
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()

        set_result = await session.call_tool("memory_set", {"key": "k", "value": "v"})
        assert set_result.isError is False
        assert json.loads(set_result.content[0].text) == {"ok": True, "key": "k"}

        get_result = await session.call_tool("memory_get", {"key": "k"})
        assert get_result.isError is False
        assert json.loads(get_result.content[0].text) == {"key": "k", "value": "v"}


async def test_unknown_tool_call_returns_is_error_not_a_protocol_error():
    mcp = build_mcp()
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.call_tool("does_not_exist", {})
        assert result.isError is True
        assert "does_not_exist" in result.content[0].text


async def test_tool_error_surfaces_as_is_error_true_over_the_wire():
    mcp = build_mcp()
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        await session.initialize()
        result = await session.call_tool("memory_get", {"key": "missing"})
        assert result.isError is True
        assert "key not found" in result.content[0].text

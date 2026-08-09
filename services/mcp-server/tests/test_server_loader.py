"""Tests for server.py's pkgutil-based auto-discovery loader — the core
"drop a file in tools/ and it's picked up" mechanism this rewrite is built
around."""

from __future__ import annotations

import importlib
import sys

import pytest
from mcp.server.fastmcp import FastMCP

from server import _register_all, build_mcp

EXPECTED_TOOL_NAMES = {
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


async def test_build_mcp_registers_every_tool_module():
    mcp = build_mcp()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOL_NAMES


async def test_registered_tool_schema_reflects_real_typed_signature():
    """Guards the functools.wraps/inspect.signature interaction in
    instrumentation.tool() — a regression here would silently degrade every
    tool's schema to a generic *args/**kwargs shape."""
    mcp = build_mcp()
    tools = {t.name: t for t in await mcp.list_tools()}

    memory_set = tools["memory_set"]
    assert memory_set.inputSchema["required"] == ["key", "value"]
    assert memory_set.inputSchema["properties"]["key"]["type"] == "string"
    assert memory_set.inputSchema["properties"]["value"]["type"] == "string"

    web_search = tools["web_search"]
    assert web_search.inputSchema["required"] == ["query"]
    assert web_search.inputSchema["properties"]["limit"]["default"] == 5


async def test_admin_routes_are_registered():
    mcp = build_mcp()
    app = mcp.streamable_http_app()
    paths = {route.path for route in app.routes}
    assert {"/health", "/config/env", "/metrics"} <= paths


def test_module_without_register_function_fails_loudly(tmp_path, monkeypatch):
    pkg_dir = tmp_path / "badpkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "broken.py").write_text("value = 1\n")

    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        badpkg = importlib.import_module("badpkg")
        with pytest.raises(RuntimeError, match="has no register"):
            _register_all(badpkg, FastMCP("test"))
    finally:
        sys.modules.pop("badpkg", None)
        sys.modules.pop("badpkg.broken", None)

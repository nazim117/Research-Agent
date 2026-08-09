from __future__ import annotations

import json
from types import ModuleType

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError


@pytest.fixture
def single_tool_mcp():
    """Build a FastMCP instance with just one tools/* module registered, for
    fast unit tests that don't need a full client/server session."""

    def _build(module: ModuleType) -> FastMCP:
        mcp = FastMCP("test")
        module.register(mcp)
        return mcp

    return _build


async def call_tool_json(mcp: FastMCP, name: str, arguments: dict) -> dict:
    """Call a tool via FastMCP's in-process call_tool() and parse the JSON
    text result. Raises mcp.server.fastmcp.exceptions.ToolError on failure —
    the same exception the real protocol layer converts into isError=true."""
    content = await mcp.call_tool(name, arguments)
    blocks = content if isinstance(content, list) else content[0]
    return json.loads(blocks[0].text)


@pytest.fixture(autouse=True)
def _reset_memory_store():
    """tools/memory.py holds a module-level dict shared across the process,
    matching the Go service's in-process singleton. Reset it between tests
    so test order doesn't matter."""
    import tools.memory as memory_module

    memory_module._store.clear()
    yield
    memory_module._store.clear()


__all__ = ["ToolError", "call_tool_json"]

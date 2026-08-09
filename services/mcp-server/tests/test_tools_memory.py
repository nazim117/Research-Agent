import pytest

import tools.memory as memory_module
from tests.conftest import ToolError, call_tool_json


async def test_set_then_get_roundtrips(single_tool_mcp):
    mcp = single_tool_mcp(memory_module)
    out = await call_tool_json(mcp, "memory_set", {"key": "goal", "value": "ship the feature"})
    assert out == {"ok": True, "key": "goal"}

    out = await call_tool_json(mcp, "memory_get", {"key": "goal"})
    assert out == {"key": "goal", "value": "ship the feature"}


async def test_set_overwrites_existing_value(single_tool_mcp):
    mcp = single_tool_mcp(memory_module)
    await call_tool_json(mcp, "memory_set", {"key": "k", "value": "first"})
    await call_tool_json(mcp, "memory_set", {"key": "k", "value": "second"})
    out = await call_tool_json(mcp, "memory_get", {"key": "k"})
    assert out["value"] == "second"


async def test_get_missing_key_raises(single_tool_mcp):
    mcp = single_tool_mcp(memory_module)
    with pytest.raises(ToolError, match="key not found"):
        await mcp.call_tool("memory_get", {"key": "nope"})


async def test_list_returns_all_entries(single_tool_mcp):
    mcp = single_tool_mcp(memory_module)
    await call_tool_json(mcp, "memory_set", {"key": "a", "value": "1"})
    await call_tool_json(mcp, "memory_set", {"key": "b", "value": "2"})
    out = await call_tool_json(mcp, "memory_list", {})
    assert out == {"count": 2, "entries": {"a": "1", "b": "2"}}


async def test_list_empty_store(single_tool_mcp):
    mcp = single_tool_mcp(memory_module)
    out = await call_tool_json(mcp, "memory_list", {})
    assert out == {"count": 0, "entries": {}}

import pytest

import tools.files as files_module
from tests.conftest import ToolError, call_tool_json


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_WORK_DIR", str(tmp_path))
    return tmp_path


async def test_write_then_read_roundtrips(single_tool_mcp):
    mcp = single_tool_mcp(files_module)
    out = await call_tool_json(mcp, "file_write", {"path": "notes.txt", "content": "hello"})
    assert out == {"ok": True, "path": "notes.txt", "bytes": 5}

    out = await call_tool_json(mcp, "file_read", {"path": "notes.txt"})
    assert out == {"path": "notes.txt", "content": "hello", "bytes": 5}


async def test_write_creates_parent_directories(single_tool_mcp, _workspace):
    mcp = single_tool_mcp(files_module)
    await call_tool_json(mcp, "file_write", {"path": "a/b/c.txt", "content": "x"})
    assert (_workspace / "a" / "b" / "c.txt").read_text() == "x"


async def test_read_nonexistent_file_raises(single_tool_mcp):
    mcp = single_tool_mcp(files_module)
    with pytest.raises(ToolError, match="read file failed"):
        await mcp.call_tool("file_read", {"path": "missing.txt"})


async def test_list_directory(single_tool_mcp, _workspace):
    mcp = single_tool_mcp(files_module)
    (_workspace / "one.txt").write_text("111")
    (_workspace / "sub").mkdir()

    out = await call_tool_json(mcp, "file_list", {"path": "."})
    assert out["count"] == 2
    entries = {e["name"]: e for e in out["entries"]}
    assert entries["one.txt"] == {"name": "one.txt", "is_dir": False, "bytes": 3}
    assert entries["sub"] == {"name": "sub", "is_dir": True}


async def test_list_defaults_to_workspace_root(single_tool_mcp, _workspace):
    mcp = single_tool_mcp(files_module)
    out = await call_tool_json(mcp, "file_list", {})
    assert out == {"path": ".", "count": 0, "entries": []}


@pytest.mark.parametrize("path", ["../../etc/passwd", "../escape.txt", "a/../../escape.txt"])
async def test_path_traversal_is_blocked(single_tool_mcp, path):
    mcp = single_tool_mcp(files_module)
    with pytest.raises(ToolError, match="escapes the workspace"):
        await mcp.call_tool("file_read", {"path": path})

# test_deep_research_loop.py — unit-level tests for the autonomous
# tool-calling research loop (deep_research.py), calling run_research_loop()
# directly rather than going through the /chat route.
#
# ScratchpadStore is real (backed by a temp SQLite file) since it's cheap
# and gives real assurance the upsert schema works; chat_fn and the mcp
# client are faked so there's no network / no real LLM.

import json
import tempfile
from unittest.mock import AsyncMock

import pytest

from actions import ActionStore
from deep_research import ALLOWED_TOOLS, MCPError, _canonical_args, run_research_loop
from scratchpad import ScratchpadStore

PROJECT_ID = "proj-dr-1"
SESSION_ID = "sess-dr-1"


@pytest.fixture
async def scratchpad():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = ScratchpadStore(path)
    await store.init()
    return store


@pytest.fixture
async def action_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = ActionStore(path)
    await store.init()
    return store


def _base_messages():
    return [{"role": "user", "content": "research the thing"}]


def _tool_call(tool: str, arguments: dict) -> str:
    return f'<<TOOL_CALL>>{json.dumps({"tool": tool, "arguments": arguments})}<<END>>'


def test_canonical_args_key_order_independence():
    assert _canonical_args({"a": 1, "b": 2}) == _canonical_args({"b": 2, "a": 1})


@pytest.mark.asyncio
async def test_normal_single_tool_call_terminates_in_final_answer(scratchpad, action_store):
    replies = [
        _tool_call("web_search", {"query": "x"}),
        "Final answer text",
    ]
    chat_fn = AsyncMock(side_effect=replies)
    mcp = AsyncMock()
    mcp.call.return_value = {"results": ["hit"]}

    result = await run_research_loop(
        _base_messages(),
        project_id=PROJECT_ID,
        session_id=SESSION_ID,
        mcp=mcp,
        scratchpad=scratchpad,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "Final answer text"
    mcp.call.assert_awaited_once_with("web_search", {"query": "x"}, project_id=PROJECT_ID)

    entries = await scratchpad.list_by_session(PROJECT_ID, SESSION_ID)
    assert len(entries) == 1
    assert entries[0].key == "step_1"
    stored = json.loads(entries[0].value)
    assert stored["tool"] == "web_search"


@pytest.mark.asyncio
async def test_step_cap_forces_final_answer(scratchpad, action_store):
    # Always request a (distinct) new tool call — never a clean final answer —
    # so the loop can only stop via the step cap, not the repeat guard.
    def _reply_for_call(n):
        return _tool_call("web_search", {"query": f"q{n}"})

    # max_steps=2 tool-call rounds, then the loop makes exactly one more
    # forced-final call — script exactly that many replies.
    chat_fn = AsyncMock(side_effect=[_reply_for_call(0), _reply_for_call(1), "forced final"])
    mcp = AsyncMock()
    mcp.call.return_value = {"ok": True}

    result = await run_research_loop(
        _base_messages(),
        project_id=PROJECT_ID,
        session_id=SESSION_ID,
        mcp=mcp,
        scratchpad=scratchpad,
        action_store=action_store,
        chat_fn=chat_fn,
        max_steps=2,
    )

    assert result.reply == "forced final"
    assert chat_fn.await_count == 3  # 2 tool-call rounds + 1 forced-final round
    last_call_messages = chat_fn.await_args_list[-1].args[0]
    assert any("No more tool calls are available" in m["content"] for m in last_call_messages)

    entries = await scratchpad.list_by_session(PROJECT_ID, SESSION_ID)
    assert len(entries) == 2


@pytest.mark.asyncio
async def test_repeat_call_guard_stops_without_reexecuting(scratchpad, action_store):
    replies = [
        _tool_call("web_search", {"query": "same"}),
        _tool_call("web_search", {"query": "same"}),  # identical call again
        "should not be reached via normal path",
    ]
    chat_fn = AsyncMock(side_effect=replies[:2] + ["forced final"])
    mcp = AsyncMock()
    mcp.call.return_value = {"ok": True}

    result = await run_research_loop(
        _base_messages(),
        project_id=PROJECT_ID,
        session_id=SESSION_ID,
        mcp=mcp,
        scratchpad=scratchpad,
        action_store=action_store,
        chat_fn=chat_fn,
        max_steps=10,
    )

    assert result.reply == "forced final"
    mcp.call.assert_awaited_once()  # only executed once, not twice
    assert chat_fn.await_count == 3  # 2 tool-call rounds (2nd detected as repeat) + forced final


@pytest.mark.asyncio
async def test_disallowed_tool_name_is_not_executed(scratchpad, action_store):
    assert "jira_add_comment" not in ALLOWED_TOOLS
    replies = [
        _tool_call("jira_add_comment", {"key": "X-1", "body": "y"}),
        "final answer after being told no",
    ]
    chat_fn = AsyncMock(side_effect=replies)
    mcp = AsyncMock()

    result = await run_research_loop(
        _base_messages(),
        project_id=PROJECT_ID,
        session_id=SESSION_ID,
        mcp=mcp,
        scratchpad=scratchpad,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "final answer after being told no"
    mcp.call.assert_not_awaited()

    entries = await scratchpad.list_by_session(PROJECT_ID, SESSION_ID)
    stored = json.loads(entries[0].value)
    assert "not allowed" in stored["result_or_error"]


@pytest.mark.asyncio
async def test_write_tool_is_queued_via_action_store_not_executed(scratchpad, action_store):
    replies = [
        _tool_call("memory_set", {"key": "k", "value": "v"}),
        "final answer after queueing write",
    ]
    chat_fn = AsyncMock(side_effect=replies)
    mcp = AsyncMock()

    result = await run_research_loop(
        _base_messages(),
        project_id=PROJECT_ID,
        session_id=SESSION_ID,
        mcp=mcp,
        scratchpad=scratchpad,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "final answer after queueing write"
    mcp.call.assert_not_awaited()

    pending = await action_store.list_for_project(PROJECT_ID, status="pending")
    assert len(pending) == 1
    assert pending[0].tool_name == "memory_set"
    assert pending[0].arguments == {"key": "k", "value": "v"}

    entries = await scratchpad.list_by_session(PROJECT_ID, SESSION_ID)
    stored = json.loads(entries[0].value)
    assert "queued for human approval" in stored["result_or_error"]


@pytest.mark.asyncio
async def test_malformed_json_does_not_crash(scratchpad, action_store):
    replies = [
        "<<TOOL_CALL>>{not valid json<<END>>",
        "final answer after malformed call",
    ]
    chat_fn = AsyncMock(side_effect=replies)
    mcp = AsyncMock()

    result = await run_research_loop(
        _base_messages(),
        project_id=PROJECT_ID,
        session_id=SESSION_ID,
        mcp=mcp,
        scratchpad=scratchpad,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "final answer after malformed call"
    mcp.call.assert_not_awaited()

    entries = await scratchpad.list_by_session(PROJECT_ID, SESSION_ID)
    stored = json.loads(entries[0].value)
    assert "malformed" in stored["result_or_error"]


@pytest.mark.asyncio
async def test_mcp_error_during_tool_call_is_caught(scratchpad, action_store):
    replies = [
        _tool_call("web_search", {"query": "x"}),
        "final answer after mcp error",
    ]
    chat_fn = AsyncMock(side_effect=replies)
    mcp = AsyncMock()
    mcp.call.side_effect = MCPError("mcp-server unreachable", status_code=502)

    result = await run_research_loop(
        _base_messages(),
        project_id=PROJECT_ID,
        session_id=SESSION_ID,
        mcp=mcp,
        scratchpad=scratchpad,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "final answer after mcp error"

    entries = await scratchpad.list_by_session(PROJECT_ID, SESSION_ID)
    stored = json.loads(entries[0].value)
    assert "mcp-server unreachable" in stored["result_or_error"]

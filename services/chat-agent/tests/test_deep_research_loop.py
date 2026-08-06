# test_deep_research_loop.py — unit-level tests for the autonomous
# tool-calling research loop (deep_research.py), calling run_research_loop()
# directly rather than going through the /chat route.
#
# TaskStore is real (backed by a temp SQLite file) since it's cheap and
# gives real assurance the create/add_step/mark_done schema works; chat_fn
# and the mcp client are faked so there's no network / no real LLM.

from unittest.mock import AsyncMock

import pytest

from actions import ActionStore
from deep_research import ALLOWED_TOOLS, MCPError, _canonical_args, run_research_loop
from tasks import TaskStore

PROJECT_ID = "proj-dr-1"
SESSION_ID = "sess-dr-1"
GOAL = "research the thing"


@pytest.fixture
async def task_store(tmp_path):
    store = TaskStore(str(tmp_path / "tasks.db"))
    await store.init()
    return store


@pytest.fixture
async def action_store(tmp_path):
    store = ActionStore(str(tmp_path / "actions.db"))
    await store.init()
    return store


async def _get_only_task(task_store):
    """Every test here runs exactly one loop invocation, so exactly one task."""
    tasks = await task_store.list_for_project(PROJECT_ID)
    assert len(tasks) == 1
    return await task_store.get(tasks[0].id)


def _base_messages():
    return [{"role": "user", "content": GOAL}]


def _tool_call(tool: str, arguments: dict) -> str:
    import json

    return f'<<TOOL_CALL>>{json.dumps({"tool": tool, "arguments": arguments})}<<END>>'


def test_canonical_args_key_order_independence():
    assert _canonical_args({"a": 1, "b": 2}) == _canonical_args({"b": 2, "a": 1})


@pytest.mark.asyncio
async def test_normal_single_tool_call_terminates_in_final_answer(task_store, action_store):
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
        goal=GOAL,
        mcp=mcp,
        task_store=task_store,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "Final answer text"
    mcp.call.assert_awaited_once_with("web_search", {"query": "x"}, project_id=PROJECT_ID)

    task = await _get_only_task(task_store)
    assert task.status == "done"
    assert task.reply == "Final answer text"
    assert task.goal == GOAL
    assert len(task.steps) == 1
    assert task.steps[0].tool_name == "web_search"


@pytest.mark.asyncio
async def test_step_cap_forces_final_answer(task_store, action_store):
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
        goal=GOAL,
        mcp=mcp,
        task_store=task_store,
        action_store=action_store,
        chat_fn=chat_fn,
        max_steps=2,
    )

    assert result.reply == "forced final"
    assert chat_fn.await_count == 3  # 2 tool-call rounds + 1 forced-final round
    last_call_messages = chat_fn.await_args_list[-1].args[0]
    assert any("No more tool calls are available" in m["content"] for m in last_call_messages)

    task = await _get_only_task(task_store)
    assert task.status == "done"
    assert len(task.steps) == 2


@pytest.mark.asyncio
async def test_repeat_call_guard_stops_without_reexecuting(task_store, action_store):
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
        goal=GOAL,
        mcp=mcp,
        task_store=task_store,
        action_store=action_store,
        chat_fn=chat_fn,
        max_steps=10,
    )

    assert result.reply == "forced final"
    mcp.call.assert_awaited_once()  # only executed once, not twice
    assert chat_fn.await_count == 3  # 2 tool-call rounds (2nd detected as repeat) + forced final


@pytest.mark.asyncio
async def test_disallowed_tool_name_is_not_executed(task_store, action_store):
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
        goal=GOAL,
        mcp=mcp,
        task_store=task_store,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "final answer after being told no"
    mcp.call.assert_not_awaited()

    task = await _get_only_task(task_store)
    assert "not allowed" in task.steps[0].result_or_error


@pytest.mark.asyncio
async def test_write_tool_is_queued_via_action_store_not_executed(task_store, action_store):
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
        goal=GOAL,
        mcp=mcp,
        task_store=task_store,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "final answer after queueing write"
    mcp.call.assert_not_awaited()

    pending = await action_store.list_for_project(PROJECT_ID, status="pending")
    assert len(pending) == 1
    assert pending[0].tool_name == "memory_set"
    assert pending[0].arguments == {"key": "k", "value": "v"}

    task = await _get_only_task(task_store)
    assert "queued for human approval" in task.steps[0].result_or_error


@pytest.mark.asyncio
async def test_malformed_json_does_not_crash(task_store, action_store):
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
        goal=GOAL,
        mcp=mcp,
        task_store=task_store,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "final answer after malformed call"
    mcp.call.assert_not_awaited()

    task = await _get_only_task(task_store)
    assert "malformed" in task.steps[0].result_or_error


@pytest.mark.asyncio
async def test_mcp_error_during_tool_call_is_caught(task_store, action_store):
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
        goal=GOAL,
        mcp=mcp,
        task_store=task_store,
        action_store=action_store,
        chat_fn=chat_fn,
    )

    assert result.reply == "final answer after mcp error"

    task = await _get_only_task(task_store)
    assert "mcp-server unreachable" in task.steps[0].result_or_error


@pytest.mark.asyncio
async def test_unexpected_exception_marks_task_failed_and_reraises(task_store, action_store):
    chat_fn = AsyncMock(side_effect=RuntimeError("boom"))
    mcp = AsyncMock()

    with pytest.raises(RuntimeError, match="boom"):
        await run_research_loop(
            _base_messages(),
            project_id=PROJECT_ID,
            session_id=SESSION_ID,
            goal=GOAL,
            mcp=mcp,
            task_store=task_store,
            action_store=action_store,
            chat_fn=chat_fn,
        )

    task = await _get_only_task(task_store)
    assert task.status == "failed"
    assert task.error == "boom"

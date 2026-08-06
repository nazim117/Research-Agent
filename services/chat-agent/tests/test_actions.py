# test_actions.py — unit tests for ActionStore and execute_action.
#
# Each test runs against a fresh temp SQLite file in tmp_path so tests are
# fully isolated. No mocking except MCPClient.call for execute_action tests.

from unittest.mock import AsyncMock

import pytest

from actions import ActionStore, execute_action


async def make_store(tmp_path, name="test.db"):
    db_path = str(tmp_path / name)
    store = ActionStore(db_path)
    await store.init()
    return store


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path):
    store = await make_store(tmp_path)
    await store.init()


@pytest.mark.asyncio
async def test_create_pending_returns_action_with_pending_status(tmp_path):
    store = await make_store(tmp_path)
    action_id = await store.create_pending("proj-1", "memory_set", {"key": "k", "value": "v"})

    action = await store.get(action_id)
    assert action is not None
    assert action.status == "pending"
    assert action.tool_name == "memory_set"
    assert action.arguments == {"key": "k", "value": "v"}
    assert action.result is None
    assert action.completed_at is None


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(tmp_path):
    store = await make_store(tmp_path)
    assert await store.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_approve_transitions_pending_to_approved(tmp_path):
    store = await make_store(tmp_path)
    action_id = await store.create_pending("proj-1", "file_write", {"path": "a.txt"})

    await store.approve(action_id)

    action = await store.get(action_id)
    assert action.status == "approved"
    assert action.completed_at is None


@pytest.mark.asyncio
async def test_approve_raises_when_not_pending(tmp_path):
    store = await make_store(tmp_path)
    action_id = await store.create_pending("proj-1", "file_write", {"path": "a.txt"})
    await store.approve(action_id)

    with pytest.raises(ValueError, match="current status is 'approved'"):
        await store.approve(action_id)


@pytest.mark.asyncio
async def test_reject_is_terminal_and_sets_completed_at(tmp_path):
    store = await make_store(tmp_path)
    action_id = await store.create_pending("proj-1", "http_request", {"url": "https://x"})

    await store.reject(action_id)

    action = await store.get(action_id)
    assert action.status == "rejected"
    assert action.completed_at is not None

    with pytest.raises(ValueError):
        await store.approve(action_id)


@pytest.mark.asyncio
async def test_mark_executed_stores_result_and_completes(tmp_path):
    store = await make_store(tmp_path)
    action_id = await store.create_pending("proj-1", "memory_set", {"key": "k"})
    await store.approve(action_id)

    await store.mark_executed(action_id, {"ok": True})

    action = await store.get(action_id)
    assert action.status == "executed"
    assert action.result == {"ok": True}
    assert action.completed_at is not None


@pytest.mark.asyncio
async def test_mark_failed_stores_error_and_completes(tmp_path):
    store = await make_store(tmp_path)
    action_id = await store.create_pending("proj-1", "memory_set", {"key": "k"})
    await store.approve(action_id)

    await store.mark_failed(action_id, "mcp-server unreachable")

    action = await store.get(action_id)
    assert action.status == "failed"
    assert action.error == "mcp-server unreachable"
    assert action.completed_at is not None


@pytest.mark.asyncio
async def test_list_for_project_scoped_and_filtered_by_status(tmp_path):
    store = await make_store(tmp_path)
    id_a = await store.create_pending("proj-a", "memory_set", {"key": "1"})
    await store.create_pending("proj-a", "file_write", {"path": "b"})
    await store.create_pending("proj-b", "memory_set", {"key": "2"})
    await store.approve(id_a)

    all_a = await store.list_for_project("proj-a")
    assert len(all_a) == 2

    pending_a = await store.list_for_project("proj-a", status="pending")
    assert len(pending_a) == 1
    assert pending_a[0].tool_name == "file_write"

    all_b = await store.list_for_project("proj-b")
    assert len(all_b) == 1


@pytest.mark.asyncio
async def test_delete_by_project_scoped(tmp_path):
    store = await make_store(tmp_path)
    await store.create_pending("proj-a", "memory_set", {"key": "1"})
    await store.create_pending("proj-b", "memory_set", {"key": "2"})

    await store.delete_by_project("proj-a")

    assert await store.list_for_project("proj-a") == []
    assert len(await store.list_for_project("proj-b")) == 1


@pytest.mark.asyncio
async def test_execute_action_calls_mcp_with_tool_name_and_arguments(tmp_path):
    store = await make_store(tmp_path)
    action_id = await store.create_pending("proj-1", "memory_set", {"key": "k", "value": "v"})
    action = await store.get(action_id)

    mock_mcp = AsyncMock()
    mock_mcp.call.return_value = {"status": "ok"}

    result = await execute_action(action, mock_mcp, "proj-1")

    mock_mcp.call.assert_awaited_once_with(
        "memory_set", {"key": "k", "value": "v"}, project_id="proj-1"
    )
    assert result == {"status": "ok"}


pytestmark = pytest.mark.asyncio

# test_tasks.py — unit tests for TaskStore.
#
# Each test runs against a fresh temp SQLite file in tmp_path so tests are
# fully isolated. No mocking, no network.

import pytest

from tasks import TaskStore


async def make_store(tmp_path, name="test.db"):
    db_path = str(tmp_path / name)
    store = TaskStore(db_path)
    await store.init()
    return store


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path):
    store = await make_store(tmp_path)
    await store.init()


@pytest.mark.asyncio
async def test_create_returns_running_task(tmp_path):
    store = await make_store(tmp_path)
    task = await store.create("proj-1", "sess-1", "research the thing")

    assert task.status == "running"
    assert task.goal == "research the thing"
    assert task.parent_task_id is None
    assert task.reply is None
    assert task.error is None
    assert task.completed_at is None


@pytest.mark.asyncio
async def test_create_with_parent_task_id(tmp_path):
    store = await make_store(tmp_path)
    parent = await store.create("proj-1", "sess-1", "parent goal")
    child = await store.create("proj-1", "sess-1", "child goal", parent_task_id=parent.id)

    fetched = await store.get(child.id)
    assert fetched.parent_task_id == parent.id


@pytest.mark.asyncio
async def test_add_step_orders_sequentially(tmp_path):
    store = await make_store(tmp_path)
    task = await store.create("proj-1", "sess-1", "goal")

    await store.add_step(task.id, "web_search", {"query": "a"}, "result a")
    await store.add_step(task.id, "web_fetch", {"url": "b"}, "result b")

    fetched = await store.get(task.id)
    assert [s.step_order for s in fetched.steps] == [0, 1]
    assert fetched.steps[0].tool_name == "web_search"
    assert fetched.steps[0].arguments == {"query": "a"}
    assert fetched.steps[1].tool_name == "web_fetch"


@pytest.mark.asyncio
async def test_add_step_allows_none_tool_and_arguments(tmp_path):
    store = await make_store(tmp_path)
    task = await store.create("proj-1", "sess-1", "goal")

    await store.add_step(task.id, None, None, "final answer text")

    fetched = await store.get(task.id)
    assert fetched.steps[0].tool_name is None
    assert fetched.steps[0].arguments is None


@pytest.mark.asyncio
async def test_mark_done_sets_reply_and_completes(tmp_path):
    store = await make_store(tmp_path)
    task = await store.create("proj-1", "sess-1", "goal")

    await store.mark_done(task.id, "final answer")

    fetched = await store.get(task.id)
    assert fetched.status == "done"
    assert fetched.reply == "final answer"
    assert fetched.completed_at is not None


@pytest.mark.asyncio
async def test_mark_failed_sets_error_and_completes(tmp_path):
    store = await make_store(tmp_path)
    task = await store.create("proj-1", "sess-1", "goal")

    await store.mark_failed(task.id, "boom")

    fetched = await store.get(task.id)
    assert fetched.status == "failed"
    assert fetched.error == "boom"
    assert fetched.completed_at is not None


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(tmp_path):
    store = await make_store(tmp_path)
    assert await store.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_list_for_project_scoped_and_filtered(tmp_path):
    store = await make_store(tmp_path)
    t1 = await store.create("proj-a", "sess-1", "goal 1")
    await store.create("proj-a", "sess-2", "goal 2")
    await store.create("proj-b", "sess-1", "goal 3")
    await store.mark_done(t1.id, "reply 1")

    all_a = await store.list_for_project("proj-a")
    assert len(all_a) == 2

    done_a = await store.list_for_project("proj-a", status="done")
    assert len(done_a) == 1
    assert done_a[0].id == t1.id

    sess1_a = await store.list_for_project("proj-a", session_id="sess-1")
    assert len(sess1_a) == 1

    all_b = await store.list_for_project("proj-b")
    assert len(all_b) == 1


@pytest.mark.asyncio
async def test_delete_by_project_cascades_steps_and_scoped(tmp_path):
    store = await make_store(tmp_path)
    t_a = await store.create("proj-a", "sess-1", "goal a")
    await store.add_step(t_a.id, "web_search", {"query": "x"}, "result")
    t_b = await store.create("proj-b", "sess-1", "goal b")

    await store.delete_by_project("proj-a")

    assert await store.list_for_project("proj-a") == []
    assert await store.get(t_a.id) is None
    assert await store.get(t_b.id) is not None


pytestmark = pytest.mark.asyncio

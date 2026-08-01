# test_workflow.py — unit tests for WorkflowStore.
#
# Each test runs against a fresh temp SQLite file in tmp_path so tests are
# fully isolated. No mocking, no network.

import json

import pytest

from workflow import WorkflowStore


async def make_store(tmp_path, name="test.db"):
    db_path = str(tmp_path / name)
    store = WorkflowStore(db_path)
    await store.init()
    return store


SAMPLE_STEPS = [
    {"tool_name": "web_search", "arguments": {"query": "PM status"}, "description": "search"},
    {"tool_name": "memory_set", "arguments": {"key": "brief", "value": "x"}, "description": None},
]


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path):
    store = await make_store(tmp_path)
    await store.init()


@pytest.mark.asyncio
async def test_save_workflow_returns_steps_in_order(tmp_path):
    store = await make_store(tmp_path)
    workflow = await store.save_workflow(
        "proj-1", "weekly-briefing", "briefing,weekly report", SAMPLE_STEPS,
    )
    assert workflow.name == "weekly-briefing"
    assert workflow.trigger == "briefing,weekly report"
    assert [s.step_order for s in workflow.steps] == [0, 1]
    assert workflow.steps[0].tool_name == "web_search"
    assert workflow.steps[1].tool_name == "memory_set"


@pytest.mark.asyncio
async def test_resave_replaces_steps_not_duplicates(tmp_path):
    store = await make_store(tmp_path)
    await store.save_workflow("proj-1", "wf", "trigger", SAMPLE_STEPS)
    new_steps = [{"tool_name": "file_read", "arguments": {"path": "a.txt"}, "description": None}]
    await store.save_workflow("proj-1", "wf", "trigger", new_steps)

    workflow = await store.get_by_name("proj-1", "wf")
    assert len(workflow.steps) == 1
    assert workflow.steps[0].tool_name == "file_read"


@pytest.mark.asyncio
async def test_get_by_name_returns_none_when_missing(tmp_path):
    store = await make_store(tmp_path)
    assert await store.get_by_name("proj-1", "does-not-exist") is None


@pytest.mark.asyncio
async def test_find_matching_returns_workflow_on_keyword_hit(tmp_path):
    store = await make_store(tmp_path)
    await store.save_workflow("proj-1", "wf", "briefing,weekly report", SAMPLE_STEPS)

    match = await store.find_matching("proj-1", "can you send me the weekly report?")
    assert match is not None
    assert match.name == "wf"


@pytest.mark.asyncio
async def test_find_matching_returns_none_on_no_hit(tmp_path):
    store = await make_store(tmp_path)
    await store.save_workflow("proj-1", "wf", "briefing,weekly report", SAMPLE_STEPS)

    match = await store.find_matching("proj-1", "what's the weather today?")
    assert match is None


@pytest.mark.asyncio
async def test_find_matching_scoped_to_project(tmp_path):
    store = await make_store(tmp_path)
    await store.save_workflow("proj-a", "wf", "briefing", SAMPLE_STEPS)

    match = await store.find_matching("proj-b", "briefing please")
    assert match is None


@pytest.mark.asyncio
async def test_list_by_project_scoped_with_nested_steps(tmp_path):
    store = await make_store(tmp_path)
    await store.save_workflow("proj-a", "wf-a", "trigger-a", SAMPLE_STEPS)
    await store.save_workflow("proj-b", "wf-b", "trigger-b", SAMPLE_STEPS)

    a_rows = await store.list_by_project("proj-a")
    assert len(a_rows) == 1
    assert a_rows[0].name == "wf-a"
    assert len(a_rows[0].steps) == 2

    b_rows = await store.list_by_project("proj-b")
    assert len(b_rows) == 1
    assert b_rows[0].name == "wf-b"


@pytest.mark.asyncio
async def test_delete_by_name_removes_only_that_workflow(tmp_path):
    store = await make_store(tmp_path)
    await store.save_workflow("proj-1", "wf-a", "trigger-a", SAMPLE_STEPS)
    await store.save_workflow("proj-1", "wf-b", "trigger-b", SAMPLE_STEPS)

    await store.delete_by_name("proj-1", "wf-a")

    remaining = await store.list_by_project("proj-1")
    assert [w.name for w in remaining] == ["wf-b"]


@pytest.mark.asyncio
async def test_delete_by_project_cascades_scoped(tmp_path):
    store = await make_store(tmp_path)
    await store.save_workflow("proj-a", "wf-a", "trigger-a", SAMPLE_STEPS)
    await store.save_workflow("proj-b", "wf-b", "trigger-b", SAMPLE_STEPS)

    await store.delete_by_project("proj-a")

    assert await store.list_by_project("proj-a") == []
    assert len(await store.list_by_project("proj-b")) == 1


@pytest.mark.asyncio
async def test_step_arguments_round_trip_as_json(tmp_path):
    store = await make_store(tmp_path)
    workflow = await store.save_workflow("proj-1", "wf", "trigger", SAMPLE_STEPS)

    assert json.loads(workflow.steps[0].arguments) == SAMPLE_STEPS[0]["arguments"]


pytestmark = pytest.mark.asyncio

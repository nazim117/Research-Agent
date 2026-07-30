# test_toolbox.py — unit tests for ToolboxStore.
#
# Each test runs against a fresh temp SQLite file in tmp_path so tests are
# fully isolated. No mocking, no network.

import asyncio
import json

import pytest

from toolbox import ToolboxStore


async def make_store(tmp_path, name="test.db"):
    db_path = str(tmp_path / name)
    store = ToolboxStore(db_path)
    await store.init()
    return store


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path):
    """Calling init() twice on the same store does not raise."""
    store = await make_store(tmp_path)
    await store.init()


@pytest.mark.asyncio
async def test_log_returns_tool_call_with_id_and_timestamp(tmp_path):
    store = await make_store(tmp_path)
    row = await store.log("proj-1", "web_search", {"query": "x"}, success=True)
    assert row.id and len(row.id) > 0
    assert row.created_at != ""


@pytest.mark.asyncio
async def test_log_success_stores_summary_no_error(tmp_path):
    store = await make_store(tmp_path)
    row = await store.log(
        "proj-1", "web_search", {"query": "x"}, success=True,
        result_summary="3 results", duration_ms=42,
    )
    assert row.success is True
    assert row.result_summary == "3 results"
    assert row.error is None
    assert row.duration_ms == 42


@pytest.mark.asyncio
async def test_log_failure_stores_error_no_summary(tmp_path):
    store = await make_store(tmp_path)
    row = await store.log(
        "proj-1", "web_search", {"query": "x"}, success=False,
        error="mcp-server unreachable",
    )
    assert row.success is False
    assert row.error == "mcp-server unreachable"
    assert row.result_summary is None


@pytest.mark.asyncio
async def test_list_by_project_scoped(tmp_path):
    """list_by_project() only returns rows for the requested project."""
    store = await make_store(tmp_path)
    await store.log("proj-a", "web_search", {"query": "a"}, success=True)
    await store.log("proj-b", "web_search", {"query": "b"}, success=True)

    a_rows = await store.list_by_project("proj-a")
    b_rows = await store.list_by_project("proj-b")

    assert len(a_rows) == 1
    assert a_rows[0].project_id == "proj-a"
    assert len(b_rows) == 1
    assert b_rows[0].project_id == "proj-b"


@pytest.mark.asyncio
async def test_list_by_project_ordered_newest_first(tmp_path):
    store = await make_store(tmp_path)
    first = await store.log("proj-1", "web_search", {"query": "first"}, success=True)
    # SQLite's datetime resolves to seconds — sleep to get a distinct timestamp.
    await asyncio.sleep(1.01)
    second = await store.log("proj-1", "web_search", {"query": "second"}, success=True)

    rows = await store.list_by_project("proj-1")
    assert [r.id for r in rows] == [second.id, first.id]


@pytest.mark.asyncio
async def test_list_by_project_empty_returns_empty_list(tmp_path):
    store = await make_store(tmp_path)
    assert await store.list_by_project("does-not-exist") == []


@pytest.mark.asyncio
async def test_list_by_project_respects_limit(tmp_path):
    store = await make_store(tmp_path)
    for i in range(3):
        await store.log("proj-1", "web_search", {"query": str(i)}, success=True)
    rows = await store.list_by_project("proj-1", limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_delete_by_project_removes_only_that_project(tmp_path):
    store = await make_store(tmp_path)
    await store.log("proj-a", "web_search", {"query": "a"}, success=True)
    await store.log("proj-b", "web_search", {"query": "b"}, success=True)

    await store.delete_by_project("proj-a")

    assert await store.list_by_project("proj-a") == []
    assert len(await store.list_by_project("proj-b")) == 1


@pytest.mark.asyncio
async def test_arguments_round_trip_as_json(tmp_path):
    store = await make_store(tmp_path)
    original = {"query": "x", "limit": 5, "nested": {"a": 1}}
    await store.log("proj-1", "web_search", original, success=True)

    rows = await store.list_by_project("proj-1")
    assert json.loads(rows[0].arguments) == original


pytestmark = pytest.mark.asyncio

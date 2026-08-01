# test_toolbox.py — unit tests for ToolboxStore.
#
# Each test runs against a fresh temp SQLite file in tmp_path so tests are
# fully isolated. No mocking, no network.

import asyncio
import json

import aiosqlite
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


@pytest.mark.asyncio
async def test_request_id_round_trips(tmp_path):
    store = await make_store(tmp_path)
    row = await store.log(
        "proj-1", "web_search", {"query": "x"}, success=True, request_id="req-abc",
    )
    assert row.request_id == "req-abc"

    rows = await store.list_by_project("proj-1")
    assert rows[0].request_id == "req-abc"


@pytest.mark.asyncio
async def test_request_id_defaults_to_none(tmp_path):
    store = await make_store(tmp_path)
    row = await store.log("proj-1", "web_search", {"query": "x"}, success=True)
    assert row.request_id is None


@pytest.mark.asyncio
async def test_get_stats_aggregates_success_and_failure(tmp_path):
    store = await make_store(tmp_path)
    await store.log("proj-1", "web_search", {}, success=True, duration_ms=100)
    await store.log("proj-1", "web_search", {}, success=True, duration_ms=200)
    await store.log("proj-1", "web_search", {}, success=False, duration_ms=50)

    stats = await store.get_stats("proj-1")
    assert len(stats) == 1
    s = stats[0]
    assert s.tool_name == "web_search"
    assert s.call_count == 3
    assert s.success_count == 2
    assert s.failure_count == 1
    assert s.success_rate == pytest.approx(2 / 3)
    assert s.avg_duration_ms == pytest.approx((100 + 200 + 50) / 3)


@pytest.mark.asyncio
async def test_get_stats_scoped_to_project(tmp_path):
    store = await make_store(tmp_path)
    await store.log("proj-a", "web_search", {}, success=True)
    await store.log("proj-b", "web_search", {}, success=False)

    a_stats = await store.get_stats("proj-a")
    assert len(a_stats) == 1
    assert a_stats[0].success_count == 1
    assert a_stats[0].failure_count == 0


@pytest.mark.asyncio
async def test_get_stats_filtered_by_tool_name(tmp_path):
    store = await make_store(tmp_path)
    await store.log("proj-1", "web_search", {}, success=True)
    await store.log("proj-1", "file_read", {}, success=True)

    stats = await store.get_stats("proj-1", tool_name="web_search")
    assert [s.tool_name for s in stats] == ["web_search"]


@pytest.mark.asyncio
async def test_get_stats_for_tool_returns_none_when_no_calls(tmp_path):
    store = await make_store(tmp_path)
    assert await store.get_stats_for_tool("proj-1", "web_search") is None


@pytest.mark.asyncio
async def test_get_stats_for_tool_returns_stats(tmp_path):
    store = await make_store(tmp_path)
    await store.log("proj-1", "web_search", {}, success=True)

    stats = await store.get_stats_for_tool("proj-1", "web_search")
    assert stats is not None
    assert stats.call_count == 1


@pytest.mark.asyncio
async def test_init_migrates_pre_request_id_table(tmp_path):
    """A tool_calls table created without request_id (pre-migration DB)
    still works after init() runs the ALTER TABLE — no crash, old rows
    read back with request_id=None, new rows can store one.
    """
    db_path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE tool_calls (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments TEXT NOT NULL,
                success INTEGER NOT NULL,
                result_summary TEXT,
                error TEXT,
                duration_ms INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "INSERT INTO tool_calls (id, project_id, tool_name, arguments, "
            "success, created_at) VALUES ('old-1', 'proj-1', 'web_search', '{}', 1, 'x')"
        )
        await db.commit()

    store = ToolboxStore(db_path)
    await store.init()

    rows = await store.list_by_project("proj-1")
    assert len(rows) == 1
    assert rows[0].request_id is None

    await store.log("proj-1", "web_search", {}, success=True, request_id="req-new")
    rows = await store.list_by_project("proj-1")
    assert any(r.request_id == "req-new" for r in rows)


pytestmark = pytest.mark.asyncio

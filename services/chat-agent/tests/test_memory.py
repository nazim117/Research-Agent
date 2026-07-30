# test_memory.py — unit tests for ConversationStore.
#
# These tests exercise only memory.py — no network, no LLM, no FastAPI app.
# Each test creates a fresh database file in pytest's tmp_path (a temporary
# directory that pytest creates and deletes automatically for each test run).
#
# Why tmp_path instead of ":memory:"?
#   aiosqlite's ":memory:" mode opens a new in-memory database for every
#   connection.  Since aiosqlite opens/closes a connection on each call,
#   ":memory:" would create a fresh database each time — init() would create
#   the table in one connection, append() would open a different (empty) DB
#   and fail.  A real file on disk persists across connections.
#
# Why @pytest.mark.asyncio?
#   ConversationStore methods are coroutines (async def).  pytest cannot await
#   them without pytest-asyncio.  The asyncio_mode = "auto" line at the bottom
#   tells pytest-asyncio to treat every async test function as asyncio without
#   needing the decorator explicitly, but we add it explicitly here for clarity.

import pytest

from memory import ConversationStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def make_store(tmp_path, name="test.db") -> ConversationStore:
    """Create and initialise a fresh ConversationStore in a temp directory."""
    db_path = str(tmp_path / name)
    store = ConversationStore(db_path)
    await store.init()
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_history_returns_empty_list(tmp_path):
    """A brand-new (project, session) pair has no history."""
    store = await make_store(tmp_path)
    result = await store.history("p1", "no-messages-yet")
    assert result == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role,content", [
    ("user",      "hello there"),
    ("assistant", "hi, how can I help?"),
    ("user",      "what is 2 + 2?"),
])
async def test_single_message_roundtrip(tmp_path, role, content):
    """A message appended can be retrieved with the correct role and content."""
    store = await make_store(tmp_path)
    await store.append("p1", "s1", role, content)
    history = await store.history("p1", "s1")
    assert len(history) == 1
    assert history[0]["role"] == role
    assert history[0]["content"] == content


@pytest.mark.asyncio
async def test_history_is_chronological(tmp_path):
    """Messages are returned oldest-first (the order the LLM expects)."""
    store = await make_store(tmp_path)
    await store.append("p1", "s1", "user",      "first")
    await store.append("p1", "s1", "assistant", "second")
    await store.append("p1", "s1", "user",      "third")

    history = await store.history("p1", "s1")
    assert [m["content"] for m in history] == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_sessions_are_isolated(tmp_path):
    """Messages in session A are not visible in session B (same project)."""
    store = await make_store(tmp_path)
    await store.append("p1", "session-A", "user", "only in A")
    await store.append("p1", "session-B", "user", "only in B")

    history_a = await store.history("p1", "session-A")
    history_b = await store.history("p1", "session-B")

    assert len(history_a) == 1
    assert history_a[0]["content"] == "only in A"

    assert len(history_b) == 1
    assert history_b[0]["content"] == "only in B"


@pytest.mark.asyncio
async def test_projects_are_isolated(tmp_path):
    """Same session_id in two different projects produces two unrelated histories.

    This is the new Step 5 invariant — without it, a conversation in project
    Alpha could leak into project Beta just because both happened to use
    "default" or some other common session_id string.
    """
    store = await make_store(tmp_path)
    await store.append("alpha", "shared-session", "user", "only in alpha")
    await store.append("beta",  "shared-session", "user", "only in beta")

    history_alpha = await store.history("alpha", "shared-session")
    history_beta  = await store.history("beta",  "shared-session")

    assert [m["content"] for m in history_alpha] == ["only in alpha"]
    assert [m["content"] for m in history_beta]  == ["only in beta"]


@pytest.mark.asyncio
@pytest.mark.parametrize("total,limit,expected_count", [
    (5, 3, 3),   # limit smaller than total — truncates
    (3, 5, 3),   # limit larger than total — returns all
    (0, 5, 0),   # empty session — returns nothing
])
async def test_history_limit(tmp_path, total, limit, expected_count):
    """history(limit=N) returns at most N messages."""
    store = await make_store(tmp_path)
    for i in range(total):
        await store.append("p1", "s1", "user", f"message {i}")

    history = await store.history("p1", "s1", limit=limit)
    assert len(history) == expected_count


@pytest.mark.asyncio
async def test_limit_returns_most_recent_messages(tmp_path):
    """When limit truncates, it keeps the MOST RECENT messages (the tail)."""
    store = await make_store(tmp_path)
    for i in range(5):
        await store.append("p1", "s1", "user", f"msg{i}")

    # With limit=3, we should see msg2, msg3, msg4 — not msg0, msg1, msg2.
    history = await store.history("p1", "s1", limit=3)
    contents = [m["content"] for m in history]
    assert contents == ["msg2", "msg3", "msg4"]


@pytest.mark.asyncio
async def test_reset_wipes_messages(tmp_path):
    """reset() drops the table (used by startup schema-wipe)."""
    store = await make_store(tmp_path)
    await store.append("p1", "s1", "user", "before reset")
    await store.reset()
    # Table was recreated so queries still work — and return nothing.
    assert await store.history("p1", "s1") == []


# Tell pytest-asyncio to run all async tests in this file under asyncio.
# This is equivalent to adding @pytest.mark.asyncio to every test above.
pytestmark = pytest.mark.asyncio
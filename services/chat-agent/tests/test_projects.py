# test_projects.py — unit tests for ProjectStore.
#
# These tests exercise only projects.py + memory.py (for the cascade test).
# No Qdrant, no LLM, no network.  Each test runs against a fresh temp SQLite
# file in tmp_path so tests are fully isolated.

import pytest

from memory import ConversationStore
from projects import SCHEMA_VERSION, ProjectStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def make_stores(tmp_path, name="test.db"):
    """Create and initialise fresh Project + Conversation stores.

    Both stores share the same SQLite file — this mirrors the production
    layout and lets us exercise the cascade-into-messages behaviour.
    """
    db_path = str(tmp_path / name)
    projects = ProjectStore(db_path)
    messages = ConversationStore(db_path)
    await projects.init()
    await messages.init()
    return projects, messages


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fresh_db_has_no_schema_version(tmp_path):
    """A brand-new DB file records no schema version until we write one."""
    projects, _ = await make_stores(tmp_path)
    assert await projects.current_version() is None


@pytest.mark.asyncio
async def test_schema_version_roundtrip(tmp_path):
    """set_version / current_version should round-trip."""
    projects, _ = await make_stores(tmp_path)
    await projects.set_version(SCHEMA_VERSION)
    assert await projects.current_version() == SCHEMA_VERSION


@pytest.mark.asyncio
async def test_schema_version_overwrites(tmp_path):
    """Calling set_version twice should overwrite, not duplicate."""
    projects, _ = await make_stores(tmp_path)
    await projects.set_version(1)
    await projects.set_version(2)
    assert await projects.current_version() == 2


# ---------------------------------------------------------------------------
# Create / list / get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_returns_project_with_id(tmp_path):
    """create() returns a Project whose id is a non-empty UUID string."""
    projects, _ = await make_stores(tmp_path)
    p = await projects.create("Alpha")
    assert p.name == "Alpha"
    assert p.id and len(p.id) > 0
    assert p.created_at != ""


@pytest.mark.asyncio
async def test_list_empty_db(tmp_path):
    """list() on a fresh DB returns an empty list, not an error."""
    projects, _ = await make_stores(tmp_path)
    assert await projects.list() == []


@pytest.mark.asyncio
async def test_list_returns_newest_first(tmp_path):
    """list() is ordered by created_at DESC.

    The test creates two projects, asserts that both come back, then asserts
    the ordering by looking at their names.  We can't rely on exact
    created_at values because SQLite's datetime('now') is second-granular.
    """
    projects, _ = await make_stores(tmp_path)
    p1 = await projects.create("First")
    # Tiny timestamp gap — SQLite's datetime('now') resolves to seconds, so
    # we sleep long enough for the next row to have a distinct timestamp.
    import asyncio
    await asyncio.sleep(1.01)
    p2 = await projects.create("Second")

    result = await projects.list()
    assert [p.name for p in result] == ["Second", "First"]
    assert [p.id for p in result] == [p2.id, p1.id]


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown(tmp_path):
    """get() on a nonexistent id returns None, not an error."""
    projects, _ = await make_stores(tmp_path)
    assert await projects.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_get_returns_the_project(tmp_path):
    """get() returns the same project that was created."""
    projects, _ = await make_stores(tmp_path)
    created = await projects.create("Alpha")
    fetched = await projects.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == "Alpha"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_name_only(tmp_path):
    """Passing name updates the project's name."""
    projects, _ = await make_stores(tmp_path)
    p = await projects.create("Alpha")
    updated = await projects.update(p.id, name="Beta")
    assert updated is not None
    assert updated.name == "Beta"


@pytest.mark.asyncio
async def test_update_unknown_project_returns_none(tmp_path):
    """update() on a nonexistent id returns None."""
    projects, _ = await make_stores(tmp_path)
    assert await projects.update("does-not-exist", name="X") is None


# ---------------------------------------------------------------------------
# Delete + cascade into messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_returns_true_on_success(tmp_path):
    """delete() returns True when the project existed."""
    projects, _ = await make_stores(tmp_path)
    p = await projects.create("Alpha")
    assert await projects.delete(p.id) is True
    # And the project is actually gone.
    assert await projects.get(p.id) is None


@pytest.mark.asyncio
async def test_delete_returns_false_for_unknown(tmp_path):
    """delete() returns False when the id was never created."""
    projects, _ = await make_stores(tmp_path)
    assert await projects.delete("does-not-exist") is False


@pytest.mark.asyncio
async def test_delete_cascades_messages(tmp_path):
    """Deleting a project removes its rows from the messages table."""
    projects, messages = await make_stores(tmp_path)
    alpha = await projects.create("Alpha")
    beta  = await projects.create("Beta")

    await messages.append(alpha.id, "s1", "user", "in alpha")
    await messages.append(beta.id,  "s1", "user", "in beta")

    assert await projects.delete(alpha.id) is True

    # Alpha's message is gone; Beta's is still there.
    assert await messages.history(alpha.id, "s1") == []
    beta_hist = await messages.history(beta.id, "s1")
    assert len(beta_hist) == 1
    assert beta_hist[0]["content"] == "in beta"


# Tell pytest-asyncio to run every async test in this file under asyncio.
pytestmark = pytest.mark.asyncio
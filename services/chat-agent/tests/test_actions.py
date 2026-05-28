# test_actions.py — unit tests for ActionStore and execute_action.
#
# All tests use a real SQLite file in a tmp_path — SyncStore.init() creates
# the `actions` table (which ActionStore reuses) before each test.
#
# execute_action tests use a FakePMIntegration to avoid any network calls.

import pytest

from sync import SyncStore
from actions import ActionStore, execute_action, validate_payload, VALID_ACTION_TYPES
from mcp_client import MCPError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def stores(tmp_path):
    """Return (SyncStore, ActionStore) sharing the same temp DB."""
    db = str(tmp_path / "test.db")
    ss = SyncStore(db)
    await ss.init()   # creates the `actions` table
    return ss, ActionStore(db)


# ---------------------------------------------------------------------------
# create_pending / list / get
# ---------------------------------------------------------------------------

async def test_create_pending_returns_id(stores):
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-1", "body": "hello", "ref_key": "jira_project_key"},
    )
    assert action_id  # non-empty UUID string


async def test_get_returns_action(stores):
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-1", "body": "hello", "ref_key": "jira_project_key"},
    )
    action = await astore.get(action_id)
    assert action is not None
    assert action.id == action_id
    assert action.status == "pending"
    assert action.action_type == "jira:add_comment"
    assert action.payload["item_id"] == "ALPHA-1"
    assert action.completed_at is None


async def test_get_unknown_returns_none(stores):
    _, astore = stores
    assert await astore.get("nonexistent-id") is None


@pytest.mark.parametrize("n", [1, 3])
async def test_list_for_project_count(stores, n):
    _, astore = stores
    for i in range(n):
        await astore.create_pending(
            "proj-x", "jira:add_comment",
            {"item_id": f"ALPHA-{i}", "body": "b", "ref_key": "jira_project_key"},
        )
    results = await astore.list_for_project("proj-x")
    assert len(results) == n


async def test_list_for_project_status_filter(stores):
    _, astore = stores
    a1 = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-1", "body": "b", "ref_key": "jira_project_key"},
    )
    a2 = await astore.create_pending(
        "proj-1", "github:add_comment",
        {"item_id": "42", "body": "b", "ref_key": "github_repo"},
    )
    await astore.reject(a2)

    pending = await astore.list_for_project("proj-1", status="pending")
    assert len(pending) == 1
    assert pending[0].id == a1

    rejected = await astore.list_for_project("proj-1", status="rejected")
    assert len(rejected) == 1
    assert rejected[0].id == a2


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------

async def test_approve_transitions_to_approved(stores):
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-1", "body": "b", "ref_key": "jira_project_key"},
    )
    await astore.approve(action_id)
    action = await astore.get(action_id)
    assert action.status == "approved"


async def test_approve_already_approved_raises(stores):
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-1", "body": "b", "ref_key": "jira_project_key"},
    )
    await astore.approve(action_id)
    with pytest.raises(ValueError, match="pending"):
        await astore.approve(action_id)


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------

async def test_reject_transitions_to_rejected(stores):
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-1", "body": "b", "ref_key": "jira_project_key"},
    )
    await astore.reject(action_id)
    action = await astore.get(action_id)
    assert action.status == "rejected"
    assert action.completed_at is not None


async def test_reject_non_pending_raises(stores):
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-1", "body": "b", "ref_key": "jira_project_key"},
    )
    await astore.approve(action_id)
    with pytest.raises(ValueError, match="pending"):
        await astore.reject(action_id)


# ---------------------------------------------------------------------------
# mark_executed
# ---------------------------------------------------------------------------

async def test_mark_executed_stores_result(stores):
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-1", "body": "b", "ref_key": "jira_project_key"},
    )
    await astore.approve(action_id)
    result = {"id": "12345", "url": "https://example.atlassian.net/browse/ALPHA-1", "created_at": "2026-01-01"}
    await astore.mark_executed(action_id, result)
    action = await astore.get(action_id)
    assert action.status == "executed"
    assert action.payload["result"]["id"] == "12345"
    assert action.completed_at is not None


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------

async def test_mark_failed_stores_error(stores):
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "github:add_comment",
        {"item_id": "42", "body": "b", "ref_key": "github_repo"},
    )
    await astore.mark_failed(action_id, "401 Unauthorized")
    action = await astore.get(action_id)
    assert action.status == "failed"
    assert action.payload["error"] == "401 Unauthorized"
    assert action.completed_at is not None


# ---------------------------------------------------------------------------
# delete_by_project
# ---------------------------------------------------------------------------

async def test_delete_by_project_only_removes_target(stores):
    _, astore = stores
    a1 = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-1", "body": "b", "ref_key": "jira_project_key"},
    )
    a2 = await astore.create_pending(
        "proj-2", "jira:add_comment",
        {"item_id": "BETA-1", "body": "b", "ref_key": "jira_project_key"},
    )
    await astore.delete_by_project("proj-1")
    assert await astore.get(a1) is None
    assert await astore.get(a2) is not None


# ---------------------------------------------------------------------------
# execute_action
# ---------------------------------------------------------------------------

class _FakeMCPClient:
    """Fake MCPClient for execute_action tests."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self._calls: list[tuple[str, dict]] = []

    async def call(self, name: str, arguments: dict) -> dict:
        self._calls.append((name, arguments))
        idx = min(len(self._calls) - 1, len(self._responses) - 1)
        result = self._responses[idx]
        if isinstance(result, MCPError):
            raise result
        return result


class _FakeProjectStore:
    async def get(self, project_id):
        from projects import Project
        return Project(
            id=project_id,
            name="Test",
            created_at="2026-01-01",
            external_refs={"jira_project_key": "ALPHA", "github_repo": "org/repo"},
        )


async def test_execute_action_jira_comment(stores):
    """Jira add_comment calls jira_add_comment with the correct key and body."""
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-5", "body": "Test comment", "ref_key": "jira_project_key"},
    )
    await astore.approve(action_id)
    action = await astore.get(action_id)

    mcp = _FakeMCPClient([{"comment_id": "cmt-99", "url": "https://example.com/comment/99", "created_at": "2026-01-01"}])
    result = await execute_action(action, mcp=mcp, project_store=_FakeProjectStore())

    assert result["id"] == "cmt-99"
    assert mcp._calls[0] == ("jira_add_comment", {"key": "ALPHA-5", "body": "Test comment"})


async def test_execute_action_github_comment(stores):
    """GitHub add_comment calls github_add_comment with repo + number."""
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "github:add_comment",
        {"item_id": "42", "body": "LGTM", "ref_key": "github_repo"},
    )
    await astore.approve(action_id)
    action = await astore.get(action_id)

    mcp = _FakeMCPClient([{"comment_id": 77, "url": "https://github.com/org/repo/issues/42#issuecomment-77", "created_at": "2026-01-01"}])
    result = await execute_action(action, mcp=mcp, project_store=_FakeProjectStore())

    assert result["id"] == "77"
    assert mcp._calls[0] == ("github_add_comment", {"repo": "org/repo", "number": 42, "body": "LGTM"})


async def test_execute_action_unknown_ref_key_raises(stores):
    """Unknown ref_key not resolvable from external_refs raises ValueError."""
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-5", "body": "b", "ref_key": "unknown_key"},
    )
    await astore.approve(action_id)
    action = await astore.get(action_id)

    mcp = _FakeMCPClient([])
    with pytest.raises(ValueError, match="No PM tool configured"):
        await execute_action(action, mcp=mcp, project_store=_FakeProjectStore())


async def test_execute_action_missing_ref_key_raises(stores):
    """Action with ref_key missing from project external_refs raises ValueError."""
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:add_comment",
        {"item_id": "ALPHA-5", "body": "b", "ref_key": "jira_project_key"},
    )
    await astore.approve(action_id)
    action = await astore.get(action_id)

    class _ProjectNoRefs:
        async def get(self, pid):
            from projects import Project
            return Project(id=pid, name="X", created_at="2026", external_refs={})

    mcp = _FakeMCPClient([])
    with pytest.raises(ValueError, match="has no 'jira_project_key'"):
        await execute_action(action, mcp=mcp, project_store=_ProjectNoRefs())


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# validate_payload
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action_type,payload,expected_missing", [
    ("jira:add_comment",  {"item_id": "P-1", "body": "hi", "ref_key": "jira_project_key"}, None),
    ("jira:add_comment",  {"item_id": "P-1", "ref_key": "jira_project_key"}, "body"),
    ("jira:create_issue", {"ref_key": "jira_project_key", "summary": "task"}, None),
    ("jira:create_issue", {"ref_key": "jira_project_key"}, "summary"),
    ("jira:create_issue", {"summary": "task"}, "ref_key"),
    ("jira:update_issue", {"item_id": "P-1", "ref_key": "jira_project_key"}, None),
    ("jira:update_issue", {"ref_key": "jira_project_key"}, "item_id"),
    ("jira:close_issue",  {"item_id": "P-1", "ref_key": "jira_project_key"}, None),
    ("jira:close_issue",  {"ref_key": "jira_project_key"}, "item_id"),
    ("github:add_comment",{"item_id": "42", "body": "x", "ref_key": "github_repo"}, None),
    ("github:add_comment",{"item_id": "42", "ref_key": "github_repo"}, "body"),
])
def test_validate_payload(action_type, payload, expected_missing):
    assert validate_payload(action_type, payload) == expected_missing


def test_all_new_action_types_in_valid_set():
    for t in ("jira:create_issue", "jira:update_issue", "jira:close_issue"):
        assert t in VALID_ACTION_TYPES


# ---------------------------------------------------------------------------
# execute_action — new Jira CRUD types
# ---------------------------------------------------------------------------

async def test_execute_action_jira_create(stores):
    """jira:create_issue calls jira_create_issue with project_key from external_refs."""
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:create_issue",
        {"ref_key": "jira_project_key", "summary": "New task", "issue_type": "Bug"},
    )
    await astore.approve(action_id)
    action = await astore.get(action_id)

    mcp = _FakeMCPClient([{"key": "ALPHA-99", "url": "https://jira/ALPHA-99"}])
    result = await execute_action(action, mcp=mcp, project_store=_FakeProjectStore())

    assert result["id"] == "ALPHA-99"
    name, args = mcp._calls[0]
    assert name == "jira_create_issue"
    assert args["project_key"] == "ALPHA"
    assert args["summary"] == "New task"
    assert args["issue_type"] == "Bug"


async def test_execute_action_jira_update(stores):
    """jira:update_issue calls jira_update_issue with key and optional fields."""
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:update_issue",
        {"item_id": "ALPHA-5", "ref_key": "jira_project_key", "summary": "New title"},
    )
    await astore.approve(action_id)
    action = await astore.get(action_id)

    mcp = _FakeMCPClient([{"key": "ALPHA-5", "url": "https://jira/ALPHA-5", "updated": True}])
    result = await execute_action(action, mcp=mcp, project_store=_FakeProjectStore())

    assert result["id"] == "ALPHA-5"
    name, args = mcp._calls[0]
    assert name == "jira_update_issue"
    assert args["key"] == "ALPHA-5"
    assert args["summary"] == "New title"


async def test_execute_action_jira_close(stores):
    """jira:close_issue calls jira_close_issue with key; optional status forwarded."""
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:close_issue",
        {"item_id": "ALPHA-7", "ref_key": "jira_project_key", "status": "Resolved"},
    )
    await astore.approve(action_id)
    action = await astore.get(action_id)

    mcp = _FakeMCPClient([{"key": "ALPHA-7", "url": "https://jira/ALPHA-7", "transitioned_to": "Resolved"}])
    result = await execute_action(action, mcp=mcp, project_store=_FakeProjectStore())

    assert result["transitioned_to"] == "Resolved"
    name, args = mcp._calls[0]
    assert name == "jira_close_issue"
    assert args["key"] == "ALPHA-7"
    assert args["status"] == "Resolved"


async def test_execute_action_jira_close_no_status(stores):
    """jira:close_issue without explicit status omits status arg (mcp defaults to Done)."""
    _, astore = stores
    action_id = await astore.create_pending(
        "proj-1", "jira:close_issue",
        {"item_id": "ALPHA-8", "ref_key": "jira_project_key"},
    )
    await astore.approve(action_id)
    action = await astore.get(action_id)

    mcp = _FakeMCPClient([{"key": "ALPHA-8", "url": "", "transitioned_to": "Done"}])
    await execute_action(action, mcp=mcp, project_store=_FakeProjectStore())

    _, args = mcp._calls[0]
    assert "status" not in args

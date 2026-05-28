# human-in-the-loop action store for PM writes.
#
# Why a separate module?
#   sync.py already owns the `actions` table as an audit log for sync events.
#   This module owns the *lifecycle* of human-approval actions — pending,
#   approved, executed, rejected, failed — using the same table.  Keeping
#   them separate avoids merging two very different concerns into sync.py.
#
# Table ownership:
#   The `actions` table is created by SyncStore.init() in sync.py.
#   This module only reads/writes rows; it never creates or alters the table.
#
# Supported action types:
#   "jira:add_comment"   — POST a comment on a Jira issue
#   "github:add_comment" — POST a comment on a GitHub issue/PR
#
# Payload shape for comment actions:
#   {"item_id": "ALPHA-12", "body": "...", "ref_key": "jira_project_key"}

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from mcp_client import MCPClient
from projects import ProjectStore

logger = logging.getLogger("uvicorn.error")

# All action types this module knows how to execute.
VALID_ACTION_TYPES = {
    "jira:add_comment",
    "jira:create_issue",
    "jira:update_issue",
    "jira:close_issue",
    "github:add_comment",
}

# ref_keys that correspond to known PM tools in the mcp-server.
_KNOWN_REF_KEYS = {"jira_project_key", "github_repo"}

# Required payload fields per action type.
# Fields listed here must be non-empty strings in the payload.
_REQUIRED_PAYLOAD_FIELDS: dict[str, list[str]] = {
    "jira:add_comment":  ["item_id", "body", "ref_key"],
    "jira:create_issue": ["ref_key", "summary"],
    "jira:update_issue": ["item_id", "ref_key"],
    "jira:close_issue":  ["item_id", "ref_key"],
    "github:add_comment": ["item_id", "body", "ref_key"],
}


def validate_payload(action_type: str, payload: dict) -> str | None:
    """Return the name of the first missing required field, or None if valid.

    Callers should raise HTTPException(400) when this returns a field name.
    """
    for field in _REQUIRED_PAYLOAD_FIELDS.get(action_type, []):
        if not payload.get(field):
            return field
    return None


@dataclass
class Action:
    """One row from the `actions` table, fully deserialised."""
    id: str
    project_id: str
    action_type: str        # maps to the `action` column
    status: str             # pending | approved | executed | rejected | failed
    payload: dict           # parsed JSON; shape varies by action_type
    created_at: str
    completed_at: str | None


def _row_to_action(row: tuple) -> Action:
    return Action(
        id=row[0],
        project_id=row[1],
        action_type=row[2],
        status=row[3],
        payload=json.loads(row[4]),
        created_at=row[5],
        completed_at=row[6],
    )


class ActionStore:
    """CRUD operations on pending / terminal action rows.

    The `actions` table is shared with SyncStore (sync.py), which writes
    `status='done'` audit rows for sync events.  ActionStore only touches
    rows in the pending/approved/executed/rejected/failed lifecycle.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def create_pending(
        self, project_id: str, action_type: str, payload: dict
    ) -> str:
        """Insert a new pending-action row. Returns the generated action id (UUID)."""
        action_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO actions (id, project_id, action, status, payload)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (action_id, project_id, action_type, json.dumps(payload)),
            )
            await db.commit()
        return action_id

    async def list_for_project(
        self, project_id: str, status: str | None = None
    ) -> list[Action]:
        """Return actions for a project, newest first. Filters by status if given."""
        async with aiosqlite.connect(self.db_path) as db:
            if status:
                cursor = await db.execute(
                    """
                    SELECT id, project_id, action, status, payload, created_at, completed_at
                    FROM actions
                    WHERE project_id = ? AND status = ?
                    ORDER BY created_at DESC
                    """,
                    (project_id, status),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT id, project_id, action, status, payload, created_at, completed_at
                    FROM actions
                    WHERE project_id = ?
                    ORDER BY created_at DESC
                    """,
                    (project_id,),
                )
            rows = await cursor.fetchall()
        return [_row_to_action(r) for r in rows]

    async def get(self, action_id: str) -> Action | None:
        """Return one action by id, or None if not found."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, project_id, action, status, payload, created_at, completed_at
                FROM actions WHERE id = ?
                """,
                (action_id,),
            )
            row = await cursor.fetchone()
        return _row_to_action(row) if row else None

    async def approve(self, action_id: str) -> None:
        """Transition pending → approved. Raises ValueError if not pending."""
        await _transition(self.db_path, action_id, from_status="pending", to_status="approved")

    async def reject(self, action_id: str) -> None:
        """Transition pending → rejected (terminal). Sets completed_at."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT status FROM actions WHERE id = ?", (action_id,)
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Action {action_id!r} not found")
            if row[0] != "pending":
                raise ValueError(
                    f"Cannot reject action {action_id!r} — "
                    f"status is {row[0]!r}, expected 'pending'"
                )
            await db.execute(
                "UPDATE actions SET status='rejected', completed_at=? WHERE id=?",
                (now, action_id),
            )
            await db.commit()

    async def mark_executed(self, action_id: str, result: dict) -> None:
        """Transition approved → executed (terminal). Merges result into payload."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT payload FROM actions WHERE id = ?", (action_id,)
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Action {action_id!r} not found")
            payload = json.loads(row[0])
            payload["result"] = result
            await db.execute(
                "UPDATE actions SET status='executed', completed_at=?, payload=? WHERE id=?",
                (now, json.dumps(payload), action_id),
            )
            await db.commit()

    async def mark_failed(self, action_id: str, error: str) -> None:
        """Transition any status → failed (terminal). Stores error string in payload."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT payload FROM actions WHERE id = ?", (action_id,)
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Action {action_id!r} not found")
            payload = json.loads(row[0])
            payload["error"] = error
            await db.execute(
                "UPDATE actions SET status='failed', completed_at=?, payload=? WHERE id=?",
                (now, json.dumps(payload), action_id),
            )
            await db.commit()

    async def reset_to_pending(self, action_id: str) -> None:
        """Transition failed → pending so the action can be re-approved."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT status FROM actions WHERE id = ?", (action_id,)
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Action {action_id!r} not found")
            if row[0] != "failed":
                raise ValueError(
                    f"Only failed actions can be retried — current status is {row[0]!r}"
                )
            await db.execute(
                "UPDATE actions SET status='pending', completed_at=NULL WHERE id=?",
                (action_id,),
            )
            await db.commit()

    async def delete_by_project(self, project_id: str) -> None:
        """Remove all action rows for a project. Called during project deletion.

        Note: SyncStore.delete_by_project() also issues the same DELETE — both
        calls are safe (the second is a no-op on an empty set).
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM actions WHERE project_id = ?", (project_id,)
            )
            await db.commit()


async def _transition(
    db_path: str, action_id: str, from_status: str, to_status: str
) -> None:
    """Generic guarded state transition shared by approve()."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT status FROM actions WHERE id = ?", (action_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise ValueError(f"Action {action_id!r} not found")
        if row[0] != from_status:
            raise ValueError(
                f"Cannot transition action {action_id!r} from {from_status!r} to "
                f"{to_status!r} — current status is {row[0]!r}"
            )
        await db.execute(
            "UPDATE actions SET status=? WHERE id=?", (to_status, action_id)
        )
        await db.commit()


async def execute_action(
    action: Action,
    mcp: MCPClient,
    project_store: ProjectStore,
) -> dict:
    """Execute an approved action by calling the appropriate mcp-server tool.

    Returns the result dict from the tool (shape: {id, url, created_at}).
    Raises on any error — the caller (route handler) is responsible for calling
    mark_failed() if an exception escapes.
    """
    ref_key = action.payload.get("ref_key")
    item_id = action.payload.get("item_id")  # absent for create actions
    body = action.payload.get("body", "")

    if not ref_key:
        raise ValueError("Action payload must contain 'ref_key'")
    # item_id is required for all action types except jira:create_issue.
    if action.action_type != "jira:create_issue" and not item_id:
        raise ValueError("Action payload must contain 'item_id'")

    project = await project_store.get(action.project_id)
    if project is None:
        raise ValueError(f"Project {action.project_id!r} not found")

    # Fallback: the LLM sometimes emits the ref *value* (e.g. "KAN") instead of
    # the ref *key* name (e.g. "jira_project_key").  Search the project's
    # external_refs for an entry whose value matches ref_key.
    if ref_key not in _KNOWN_REF_KEYS:
        for k, v in (project.external_refs or {}).items():
            if v == ref_key and k in _KNOWN_REF_KEYS:
                logger.warning(
                    "ref_key %r looks like a ref value; resolved to integration key %r",
                    ref_key, k,
                )
                ref_key = k
                break

    if ref_key not in _KNOWN_REF_KEYS:
        raise ValueError(
            f"No PM tool configured for ref_key {ref_key!r}. "
            "Supported keys: jira_project_key, github_repo."
        )

    ref_value = (project.external_refs or {}).get(ref_key)
    if not ref_value:
        raise ValueError(
            f"Project {action.project_id!r} has no {ref_key!r} in external_refs"
        )

    if action.action_type == "jira:add_comment":
        result = await mcp.call("jira_add_comment", {"key": item_id, "body": body})
        return {
            "id": result.get("comment_id", ""),
            "url": result.get("url", ""),
            "created_at": result.get("created_at", ""),
        }

    if action.action_type == "jira:create_issue":
        mcp_args: dict = {
            "project_key": ref_value,
            "summary": action.payload.get("summary", ""),
        }
        if action.payload.get("issue_type"):
            mcp_args["issue_type"] = action.payload["issue_type"]
        if action.payload.get("description"):
            mcp_args["description"] = action.payload["description"]
        result = await mcp.call("jira_create_issue", mcp_args)
        return {
            "id": result.get("key", ""),
            "url": result.get("url", ""),
            "created_at": "",
        }

    if action.action_type == "jira:update_issue":
        mcp_args = {"key": item_id}
        if action.payload.get("summary"):
            mcp_args["summary"] = action.payload["summary"]
        if action.payload.get("description"):
            mcp_args["description"] = action.payload["description"]
        result = await mcp.call("jira_update_issue", mcp_args)
        return {
            "id": item_id,
            "url": result.get("url", ""),
            "created_at": "",
        }

    if action.action_type == "jira:close_issue":
        mcp_args = {"key": item_id}
        if action.payload.get("status"):
            mcp_args["status"] = action.payload["status"]
        result = await mcp.call("jira_close_issue", mcp_args)
        return {
            "id": item_id,
            "url": result.get("url", ""),
            "transitioned_to": result.get("transitioned_to", ""),
            "created_at": "",
        }

    if action.action_type == "github:add_comment":
        result = await mcp.call(
            "github_add_comment",
            {"repo": ref_value, "number": int(item_id), "body": body},
        )
        return {
            "id": str(result.get("comment_id", "")),
            "url": result.get("url", ""),
            "created_at": result.get("created_at", ""),
        }

    raise ValueError(f"Unknown action_type {action.action_type!r}")

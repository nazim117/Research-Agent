# actions.py — human-in-the-loop approval gate for agent-proposed writes.
#
# deep_research.py's loop may request a write tool (memory_set, file_write,
# http_request) but must never execute it directly — this module owns the
# pending -> approved -> executed (or rejected / failed) lifecycle that
# stands between a proposal and a real mcp-server call.
#
# Unlike the pre-d5038c9e version of this module, there is no vendor-specific
# branching: today's write tools all take flat argument dicts already
# validated by mcp-server's own tool schemas, so execute_action() is a single
# generic mcp.call().

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from mcp_client import MCPClient


@dataclass
class Action:
    """One row from the `agent_actions` table, fully deserialised."""

    id: str
    project_id: str
    tool_name: str
    status: str  # pending | approved | rejected | executed | failed
    arguments: dict
    result: dict | None
    error: str | None
    created_at: str
    completed_at: str | None


def _row_to_action(row: tuple) -> Action:
    return Action(
        id=row[0],
        project_id=row[1],
        tool_name=row[2],
        status=row[3],
        arguments=json.loads(row[4]),
        result=json.loads(row[5]) if row[5] else None,
        error=row[6],
        created_at=row[7],
        completed_at=row[8],
    )


class ActionStore:
    """Manages the agent_actions table.

    Lives in the same SQLite file as projects + messages, following the same
    pattern as WorkflowStore/ToolboxStore.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the table if it does not exist. Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_actions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_actions_project "
                "ON agent_actions(project_id, status, created_at)"
            )
            await db.commit()

    async def create_pending(
        self, project_id: str, tool_name: str, arguments: dict
    ) -> str:
        """Insert a new pending-action row. Returns the generated action id."""
        action_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO agent_actions (id, project_id, tool_name, status, "
                "arguments, result, error, created_at, completed_at) "
                "VALUES (?, ?, ?, 'pending', ?, NULL, NULL, ?, NULL)",
                (action_id, project_id, tool_name, json.dumps(arguments, default=str), now),
            )
            await db.commit()
        return action_id

    async def get(self, action_id: str) -> Action | None:
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT id, project_id, tool_name, status, arguments, result, "
            "error, created_at, completed_at FROM agent_actions WHERE id = ?",
            (action_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_action(row) if row else None

    async def list_for_project(
        self, project_id: str, status: str | None = None
    ) -> list[Action]:
        sql = (
            "SELECT id, project_id, tool_name, status, arguments, result, "
            "error, created_at, completed_at FROM agent_actions WHERE project_id = ?"
        )
        params: tuple = (project_id,)
        if status:
            sql += " AND status = ?"
            params = (project_id, status)
        sql += " ORDER BY created_at DESC"
        async with aiosqlite.connect(self.db_path) as db, db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_action(r) for r in rows]

    async def approve(self, action_id: str) -> None:
        """Transition pending -> approved. Raises ValueError if not pending."""
        await self._transition(action_id, from_status="pending", to_status="approved")

    async def reject(self, action_id: str) -> None:
        """Transition pending -> rejected (terminal)."""
        await self._transition(action_id, from_status="pending", to_status="rejected", terminal=True)

    async def mark_executed(self, action_id: str, result: dict) -> None:
        """Transition approved -> executed (terminal). Stores the tool result."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE agent_actions SET status='executed', result=?, completed_at=? WHERE id=?",
                (json.dumps(result, default=str), now, action_id),
            )
            await db.commit()

    async def mark_failed(self, action_id: str, error: str) -> None:
        """Transition any status -> failed (terminal). Stores the error string."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE agent_actions SET status='failed', error=?, completed_at=? WHERE id=?",
                (error, now, action_id),
            )
            await db.commit()

    async def _transition(
        self, action_id: str, from_status: str, to_status: str, terminal: bool = False
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT status FROM agent_actions WHERE id = ?", (action_id,)
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Action {action_id!r} not found")
            if row[0] != from_status:
                raise ValueError(
                    f"Cannot transition action {action_id!r} from {from_status!r} to "
                    f"{to_status!r} — current status is {row[0]!r}"
                )
            if terminal:
                now = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    "UPDATE agent_actions SET status=?, completed_at=? WHERE id=?",
                    (to_status, now, action_id),
                )
            else:
                await db.execute(
                    "UPDATE agent_actions SET status=? WHERE id=?", (to_status, action_id)
                )
            await db.commit()

    async def delete_by_project(self, project_id: str) -> None:
        """Remove all action rows for a project (called on project delete)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM agent_actions WHERE project_id = ?", (project_id,)
            )
            await db.commit()


async def execute_action(action: Action, mcp: MCPClient, project_id: str) -> dict:
    """Execute an approved action by calling the appropriate mcp-server tool.

    Generic — no per-tool branching needed since memory_set/file_write/
    http_request all take flat argument dicts. Raises MCPError on failure;
    the caller is responsible for calling mark_failed() if it escapes.
    """
    return await mcp.call(action.tool_name, action.arguments, project_id=project_id)

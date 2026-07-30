# toolbox.py — durable log of every tool call the agent makes.
#
# This is pure logging/observability infrastructure: no learning logic, no
# new tools. It's the foundation for a later toolbox-learning feature and for
# workflow-replay (both out of scope here) — see MEMORY_PLAN.md.
#
# Rows are project_id-scoped like every other table in this repo. Arguments
# and result summaries are stored as bounded, generic JSON/text — callers are
# responsible for not passing tool arguments containing credentials (which
# should never happen by construction: mcp-server holds all vendor secrets
# server-side, per mcp_client.py's header comment).

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

_MAX_FIELD_LEN = 500


@dataclass
class ToolCall:
    id: str
    project_id: str
    tool_name: str
    arguments: str
    success: bool
    result_summary: str | None
    error: str | None
    duration_ms: int | None
    created_at: str


class ToolboxStore:
    """Manages the tool_calls table.

    Lives in the same SQLite file as projects + messages, following the same
    pattern as TranscriptStore/DocumentStateStore.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the table if it does not exist. Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_calls (
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
                "CREATE INDEX IF NOT EXISTS idx_tool_calls_project "
                "ON tool_calls(project_id, created_at)"
            )
            await db.commit()

    async def log(
        self,
        project_id: str,
        tool_name: str,
        arguments: dict,
        success: bool,
        result_summary: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> ToolCall:
        """Insert one tool-call record. Pure storage — no control flow, no
        exceptions related to the tool call itself.
        """
        row = ToolCall(
            id=str(uuid.uuid4()),
            project_id=project_id,
            tool_name=tool_name,
            arguments=json.dumps(arguments, default=str),
            success=success,
            result_summary=result_summary[:_MAX_FIELD_LEN] if result_summary else None,
            error=error[:_MAX_FIELD_LEN] if error else None,
            duration_ms=duration_ms,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO tool_calls (id, project_id, tool_name, arguments, "
                "success, result_summary, error, duration_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.id,
                    row.project_id,
                    row.tool_name,
                    row.arguments,
                    int(row.success),
                    row.result_summary,
                    row.error,
                    row.duration_ms,
                    row.created_at,
                ),
            )
            await db.commit()
        return row

    async def list_by_project(self, project_id: str, limit: int = 100) -> list[ToolCall]:
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT id, project_id, tool_name, arguments, success, "
            "result_summary, error, duration_ms, created_at FROM tool_calls "
            "WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            ToolCall(
                id=r[0],
                project_id=r[1],
                tool_name=r[2],
                arguments=r[3],
                success=bool(r[4]),
                result_summary=r[5],
                error=r[6],
                duration_ms=r[7],
                created_at=r[8],
            )
            for r in rows
        ]

    async def delete_by_project(self, project_id: str) -> None:
        """Remove all logged tool calls for a project (called on project delete)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM tool_calls WHERE project_id = ?", (project_id,)
            )
            await db.commit()

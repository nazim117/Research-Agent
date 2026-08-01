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
    request_id: str | None = None


@dataclass
class ToolStats:
    """Aggregate success/failure stats for one tool, scoped to a project.

    The read side of toolbox memory: lets a caller ask "does this tool
    actually work for this project?" instead of just logging that it ran.
    """

    tool_name: str
    call_count: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_duration_ms: float | None
    last_used_at: str


class ToolboxStore:
    """Manages the tool_calls table.

    Lives in the same SQLite file as projects + messages, following the same
    pattern as TranscriptStore/DocumentStateStore.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the table if it does not exist. Idempotent.

        `request_id` was added after the table's initial release — for a
        pre-existing DB created before this column existed, add it via a
        best-effort ALTER TABLE, swallowing the "duplicate column" error on
        every subsequent boot. Not gated by projects.SCHEMA_VERSION (this
        table never has been), so there's no wipe path to reach it instead.
        """
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
                    created_at TEXT NOT NULL,
                    request_id TEXT
                )
                """
            )
            try:
                await db.execute("ALTER TABLE tool_calls ADD COLUMN request_id TEXT")
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
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
        request_id: str | None = None,
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
            request_id=request_id,
        )
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO tool_calls (id, project_id, tool_name, arguments, "
                "success, result_summary, error, duration_ms, created_at, request_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    row.request_id,
                ),
            )
            await db.commit()
        return row

    async def list_by_project(self, project_id: str, limit: int = 100) -> list[ToolCall]:
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT id, project_id, tool_name, arguments, success, "
            "result_summary, error, duration_ms, created_at, request_id FROM tool_calls "
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
                request_id=r[9],
            )
            for r in rows
        ]

    async def get_stats(
        self, project_id: str, tool_name: str | None = None
    ) -> list[ToolStats]:
        """Aggregate success/failure counts per tool for a project."""
        sql = (
            "SELECT tool_name, COUNT(*), SUM(success), AVG(duration_ms), MAX(created_at) "
            "FROM tool_calls WHERE project_id = ?"
        )
        params: tuple = (project_id,)
        if tool_name:
            sql += " AND tool_name = ?"
            params = (project_id, tool_name)
        sql += " GROUP BY tool_name"
        async with aiosqlite.connect(self.db_path) as db, db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        stats = []
        for name, call_count, success_count, avg_duration_ms, last_used_at in rows:
            success_count = int(success_count or 0)
            stats.append(
                ToolStats(
                    tool_name=name,
                    call_count=call_count,
                    success_count=success_count,
                    failure_count=call_count - success_count,
                    success_rate=success_count / call_count if call_count else 0.0,
                    avg_duration_ms=avg_duration_ms,
                    last_used_at=last_used_at,
                )
            )
        return stats

    async def get_stats_for_tool(
        self, project_id: str, tool_name: str
    ) -> ToolStats | None:
        """Stats for a single tool — used to annotate workflow steps in the
        /chat prompt without fetching a full per-project list per step.
        """
        rows = await self.get_stats(project_id, tool_name=tool_name)
        return rows[0] if rows else None

    async def delete_by_project(self, project_id: str) -> None:
        """Remove all logged tool calls for a project (called on project delete)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM tool_calls WHERE project_id = ?", (project_id,)
            )
            await db.commit()

# tasks.py — durable record of autonomous agent runs.
#
# Replaces scratchpad.py: a scratchpad entry captured the same shape of
# data (tool/arguments/result-or-error per step) but was wiped at the start
# of every deep_research invocation. agent_tasks/agent_task_steps keep that
# history permanently, so a run can be inspected after the fact and so a
# future scheduler (see AUTONOMY_ROADMAP.md) has something durable to
# create/track work against.
#
# Lifecycle: a task is created (status='running') the instant deep_research
# starts working on it — the loop is still synchronous within one HTTP
# request, so there's no meaningful 'pending' state yet. It ends in 'done'
# (with the final reply) or 'failed' (with an error), both terminal.
#
# parent_task_id is a forward-compat hook for a future sub-agent
# architecture (parallel/specialized research sub-loops) — unused by any
# code path today, always None.

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiosqlite


@dataclass
class TaskStep:
    id: str
    task_id: str
    step_order: int
    tool_name: str | None
    arguments: dict | None
    result_or_error: str
    created_at: str


@dataclass
class Task:
    id: str
    project_id: str
    session_id: str
    goal: str
    status: str  # running | done | failed
    reply: str | None
    error: str | None
    parent_task_id: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    steps: list[TaskStep] = field(default_factory=list)


def _row_to_task(row: tuple) -> Task:
    return Task(
        id=row[0],
        project_id=row[1],
        session_id=row[2],
        goal=row[3],
        status=row[4],
        reply=row[5],
        error=row[6],
        parent_task_id=row[7],
        created_at=row[8],
        updated_at=row[9],
        completed_at=row[10],
    )


def _row_to_step(row: tuple) -> TaskStep:
    return TaskStep(
        id=row[0],
        task_id=row[1],
        step_order=row[2],
        tool_name=row[3],
        arguments=json.loads(row[4]) if row[4] else None,
        result_or_error=row[5],
        created_at=row[6],
    )


class TaskStore:
    """Manages the agent_tasks + agent_task_steps tables.

    Lives in the same SQLite file as everything else, following the same
    parent/child pattern as WorkflowStore/workflow_steps.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create both tables + indexes if they do not exist. Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reply TEXT,
                    error TEXT,
                    parent_task_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_tasks_project "
                "ON agent_tasks(project_id, status, created_at)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_task_steps (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_order INTEGER NOT NULL,
                    tool_name TEXT,
                    arguments TEXT,
                    result_or_error TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_task_steps_task "
                "ON agent_task_steps(task_id, step_order)"
            )
            await db.commit()

    async def create(
        self,
        project_id: str,
        session_id: str,
        goal: str,
        parent_task_id: str | None = None,
    ) -> Task:
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO agent_tasks (id, project_id, session_id, goal, "
                "status, reply, error, parent_task_id, created_at, updated_at, "
                "completed_at) VALUES (?, ?, ?, ?, 'running', NULL, NULL, ?, ?, ?, NULL)",
                (task_id, project_id, session_id, goal, parent_task_id, now, now),
            )
            await db.commit()
        return Task(
            id=task_id,
            project_id=project_id,
            session_id=session_id,
            goal=goal,
            status="running",
            reply=None,
            error=None,
            parent_task_id=parent_task_id,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )

    async def add_step(
        self,
        task_id: str,
        tool_name: str | None,
        arguments: dict | None,
        result_or_error: str,
    ) -> TaskStep:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COALESCE(MAX(step_order), -1) + 1 FROM agent_task_steps "
                "WHERE task_id = ?",
                (task_id,),
            )
            (step_order,) = await cursor.fetchone()

            step_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            arguments_json = json.dumps(arguments, default=str) if arguments is not None else None
            await db.execute(
                "INSERT INTO agent_task_steps (id, task_id, step_order, tool_name, "
                "arguments, result_or_error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (step_id, task_id, step_order, tool_name, arguments_json, result_or_error, now),
            )
            await db.execute(
                "UPDATE agent_tasks SET updated_at = ? WHERE id = ?", (now, task_id)
            )
            await db.commit()

        return TaskStep(
            id=step_id,
            task_id=task_id,
            step_order=step_order,
            tool_name=tool_name,
            arguments=arguments,
            result_or_error=result_or_error,
            created_at=now,
        )

    async def mark_done(self, task_id: str, reply: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE agent_tasks SET status='done', reply=?, updated_at=?, "
                "completed_at=? WHERE id=?",
                (reply, now, now, task_id),
            )
            await db.commit()

    async def mark_failed(self, task_id: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE agent_tasks SET status='failed', error=?, updated_at=?, "
                "completed_at=? WHERE id=?",
                (error, now, now, task_id),
            )
            await db.commit()

    async def get(self, task_id: str) -> Task | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, project_id, session_id, goal, status, reply, error, "
                "parent_task_id, created_at, updated_at, completed_at "
                "FROM agent_tasks WHERE id = ?",
                (task_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            async with db.execute(
                "SELECT id, task_id, step_order, tool_name, arguments, "
                "result_or_error, created_at FROM agent_task_steps "
                "WHERE task_id = ? ORDER BY step_order ASC",
                (task_id,),
            ) as cur:
                step_rows = await cur.fetchall()

        task = _row_to_task(row)
        task.steps = [_row_to_step(r) for r in step_rows]
        return task

    async def list_for_project(
        self,
        project_id: str,
        status: str | None = None,
        session_id: str | None = None,
    ) -> list[Task]:
        sql = (
            "SELECT id, project_id, session_id, goal, status, reply, error, "
            "parent_task_id, created_at, updated_at, completed_at "
            "FROM agent_tasks WHERE project_id = ?"
        )
        params: list = [project_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY created_at DESC"

        async with aiosqlite.connect(self.db_path) as db, db.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]

    async def delete_by_project(self, project_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            task_ids = await db.execute_fetchall(
                "SELECT id FROM agent_tasks WHERE project_id = ?", (project_id,)
            )
            for (task_id,) in task_ids:
                await db.execute(
                    "DELETE FROM agent_task_steps WHERE task_id = ?", (task_id,)
                )
            await db.execute(
                "DELETE FROM agent_tasks WHERE project_id = ?", (project_id,)
            )
            await db.commit()

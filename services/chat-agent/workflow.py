# workflow.py — procedural memory: named, explicitly-authored step sequences.
#
# A workflow is a stored procedure (e.g. "weekly PM briefing"): a name, a
# trigger (comma-separated keywords/phrases matched against incoming chat
# messages), and an ordered list of steps, each a (tool_name, arguments,
# description) tuple referencing the same tool names mcp-server exposes
# (see mcp_client.py / registry.go).
#
# Scope for this pass (see MEMORY_PLAN.md): workflows are created explicitly
# via the API, not derived from tool-call history — the current /chat flow
# only ever makes one tool call per turn, so there's no multi-step sequence
# to auto-derive from. Replay is read-only: a matched workflow's steps are
# folded into the LLM prompt as guidance context, the same mechanism as doc
# chunks and web results (main.py). Nothing executes automatically.
#
# Idempotency: saving a workflow with the same (project_id, name) replaces
# its steps rather than duplicating them — same delete-then-rewrite pattern
# as TranscriptStore.delete_by_source + reinsert.

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiosqlite


@dataclass
class WorkflowStep:
    id: str
    workflow_id: str
    step_order: int
    tool_name: str
    arguments: str  # JSON-encoded dict, same convention as toolbox.py
    description: str | None
    created_at: str


@dataclass
class Workflow:
    id: str
    project_id: str
    name: str
    trigger: str
    description: str | None
    created_at: str
    steps: list[WorkflowStep] = field(default_factory=list)


class WorkflowStore:
    """Manages the workflows + workflow_steps tables.

    Lives in the same SQLite file as projects + messages, following the same
    pattern as TranscriptStore/ToolboxStore.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create both tables + indexes if they do not exist. Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflows_project "
                "ON workflows(project_id, name)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_steps (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    step_order INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_steps_workflow "
                "ON workflow_steps(workflow_id, step_order)"
            )
            await db.commit()

    # -- save --------------------------------------------------------------
    async def save_workflow(
        self,
        project_id: str,
        name: str,
        trigger: str,
        steps: list[dict],
        description: str | None = None,
    ) -> Workflow:
        """Create or replace a workflow by (project_id, name).

        Re-saving a workflow with the same name deletes its prior row + steps
        first, so repeat saves don't duplicate — same idempotency contract as
        TranscriptStore.delete_by_source + save_extracted.
        """
        now = datetime.now(timezone.utc).isoformat()
        workflow_id = str(uuid.uuid4())

        async with aiosqlite.connect(self.db_path) as db:
            existing = await db.execute_fetchall(
                "SELECT id FROM workflows WHERE project_id = ? AND name = ?",
                (project_id, name),
            )
            for (old_id,) in existing:
                await db.execute(
                    "DELETE FROM workflow_steps WHERE workflow_id = ?", (old_id,)
                )
            await db.execute(
                "DELETE FROM workflows WHERE project_id = ? AND name = ?",
                (project_id, name),
            )

            await db.execute(
                "INSERT INTO workflows (id, project_id, name, trigger, "
                "description, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (workflow_id, project_id, name, trigger, description, now),
            )

            step_rows: list[WorkflowStep] = []
            for i, step in enumerate(steps):
                step_id = str(uuid.uuid4())
                arguments = json.dumps(step.get("arguments") or {}, default=str)
                step_description = step.get("description")
                await db.execute(
                    "INSERT INTO workflow_steps (id, workflow_id, step_order, "
                    "tool_name, arguments, description, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        step_id,
                        workflow_id,
                        i,
                        step["tool_name"],
                        arguments,
                        step_description,
                        now,
                    ),
                )
                step_rows.append(
                    WorkflowStep(
                        id=step_id,
                        workflow_id=workflow_id,
                        step_order=i,
                        tool_name=step["tool_name"],
                        arguments=arguments,
                        description=step_description,
                        created_at=now,
                    )
                )

            await db.commit()

        return Workflow(
            id=workflow_id,
            project_id=project_id,
            name=name,
            trigger=trigger,
            description=description,
            created_at=now,
            steps=step_rows,
        )

    # -- read ----------------------------------------------------------------
    async def _load_steps(self, db: aiosqlite.Connection, workflow_id: str) -> list[WorkflowStep]:
        async with db.execute(
            "SELECT id, workflow_id, step_order, tool_name, arguments, "
            "description, created_at FROM workflow_steps "
            "WHERE workflow_id = ? ORDER BY step_order ASC",
            (workflow_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [WorkflowStep(*r) for r in rows]

    async def get_by_name(self, project_id: str, name: str) -> Workflow | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, project_id, name, trigger, description, created_at "
                "FROM workflows WHERE project_id = ? AND name = ?",
                (project_id, name),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            steps = await self._load_steps(db, row[0])
        return Workflow(
            id=row[0],
            project_id=row[1],
            name=row[2],
            trigger=row[3],
            description=row[4],
            created_at=row[5],
            steps=steps,
        )

    async def list_by_project(self, project_id: str) -> list[Workflow]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, project_id, name, trigger, description, created_at "
                "FROM workflows WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ) as cur:
                rows = await cur.fetchall()
            workflows = []
            for r in rows:
                steps = await self._load_steps(db, r[0])
                workflows.append(
                    Workflow(
                        id=r[0],
                        project_id=r[1],
                        name=r[2],
                        trigger=r[3],
                        description=r[4],
                        created_at=r[5],
                        steps=steps,
                    )
                )
        return workflows

    async def find_matching(self, project_id: str, message: str) -> Workflow | None:
        """v1 heuristic: case-insensitive substring match against comma-
        separated trigger keywords. Returns the workflow with the most
        keyword hits, or None if no workflow's trigger matches at all.

        Semantic matching (same approach as rag.py) is future work — this is
        deliberately simple since it's the first version of workflow replay.
        """
        message_lower = message.lower()
        best: Workflow | None = None
        best_hits = 0
        for workflow in await self.list_by_project(project_id):
            keywords = [k.strip().lower() for k in workflow.trigger.split(",") if k.strip()]
            hits = sum(1 for k in keywords if k in message_lower)
            if hits > best_hits:
                best = workflow
                best_hits = hits
        return best

    # -- delete ----------------------------------------------------------------
    async def delete_by_name(self, project_id: str, name: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            existing = await db.execute_fetchall(
                "SELECT id FROM workflows WHERE project_id = ? AND name = ?",
                (project_id, name),
            )
            for (workflow_id,) in existing:
                await db.execute(
                    "DELETE FROM workflow_steps WHERE workflow_id = ?", (workflow_id,)
                )
            await db.execute(
                "DELETE FROM workflows WHERE project_id = ? AND name = ?",
                (project_id, name),
            )
            await db.commit()

    async def delete_by_project(self, project_id: str) -> None:
        """Remove all workflows + steps for a project (called on project delete)."""
        async with aiosqlite.connect(self.db_path) as db:
            workflow_ids = await db.execute_fetchall(
                "SELECT id FROM workflows WHERE project_id = ?", (project_id,)
            )
            for (workflow_id,) in workflow_ids:
                await db.execute(
                    "DELETE FROM workflow_steps WHERE workflow_id = ?", (workflow_id,)
                )
            await db.execute(
                "DELETE FROM workflows WHERE project_id = ?", (project_id,)
            )
            await db.commit()

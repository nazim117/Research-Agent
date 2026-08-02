# scratchpad.py — working memory for the deep-research tool-calling loop.
#
# Scope (see MEMORY_PLAN.md): a scratchpad is short-lived, per-(project,
# session) task scaffolding for the autonomous multi-step research loop
# (deep_research.py) — distinct from conversation history (memory.py) and
# from workflows (procedural, hand-authored). Each entry is one step's
# tool-call + result, or a synthetic error placeholder. Cleared at the START
# of every deep_research=true /chat request, then repopulated as the loop
# runs, so it always reflects only the CURRENT research task, not a running
# log across turns. It is loop-only working memory for this pass — plain
# chat turns neither read nor clear it; inspect it via
# GET /projects/{id}/scratchpad for debugging.
#
# Upsert semantics: (project_id, session_id, key) is unique — writing the
# same key again overwrites, it does not append a duplicate row.

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite


@dataclass
class ScratchpadEntry:
    id: str
    project_id: str
    session_id: str
    key: str
    value: str
    created_at: str
    updated_at: str


class ScratchpadStore:
    """Manages the scratchpad_entries table.

    Lives in the same SQLite file as everything else, following the same
    pattern as WorkflowStore/ToolboxStore.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the table + unique index if they do not exist. Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS scratchpad_entries (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_scratchpad_unique "
                "ON scratchpad_entries(project_id, session_id, key)"
            )
            await db.commit()

    async def set_entry(
        self, project_id: str, session_id: str, key: str, value: str
    ) -> ScratchpadEntry:
        """Upsert by (project_id, session_id, key).

        Same key overwrites value + updated_at; created_at is preserved from
        the original insert.
        """
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, created_at FROM scratchpad_entries "
                "WHERE project_id = ? AND session_id = ? AND key = ?",
                (project_id, session_id, key),
            ) as cur:
                row = await cur.fetchone()
            if row:
                entry_id, created_at = row
                await db.execute(
                    "UPDATE scratchpad_entries SET value = ?, updated_at = ? "
                    "WHERE id = ?",
                    (value, now, entry_id),
                )
            else:
                entry_id = str(uuid.uuid4())
                created_at = now
                await db.execute(
                    "INSERT INTO scratchpad_entries "
                    "(id, project_id, session_id, key, value, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (entry_id, project_id, session_id, key, value, created_at, now),
                )
            await db.commit()
        return ScratchpadEntry(
            id=entry_id,
            project_id=project_id,
            session_id=session_id,
            key=key,
            value=value,
            created_at=created_at,
            updated_at=now,
        )

    async def list_by_session(
        self, project_id: str, session_id: str
    ) -> list[ScratchpadEntry]:
        """Ordered by created_at ASC so steps read in the order they happened."""
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT id, project_id, session_id, key, value, created_at, "
            "updated_at FROM scratchpad_entries "
            "WHERE project_id = ? AND session_id = ? ORDER BY created_at ASC",
            (project_id, session_id),
        ) as cur:
            rows = await cur.fetchall()
        return [ScratchpadEntry(*r) for r in rows]

    async def clear_session(self, project_id: str, session_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM scratchpad_entries WHERE project_id = ? AND session_id = ?",
                (project_id, session_id),
            )
            await db.commit()

    async def delete_by_project(self, project_id: str) -> None:
        """Remove all scratchpad entries for a project (project delete cascade)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM scratchpad_entries WHERE project_id = ?", (project_id,)
            )
            await db.commit()

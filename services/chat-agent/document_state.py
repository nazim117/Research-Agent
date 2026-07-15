# document_state.py — per-document enabled/disabled flag.
#
# Documents have no id of their own (see rag.py) — a document is identified
# by its `source` label within a project.  This table records which sources
# a user has toggled off, so RAG retrieval can skip them without deleting
# their chunks.  A row's absence means enabled (the default for anything
# freshly ingested).

import aiosqlite


class DocumentStateStore:
    """Manages the document_state table.

    Lives in the same SQLite file as projects + messages, following the same
    pattern as SyncStore (sync.py) and TranscriptStore.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the table if it does not exist. Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS document_state (
                    project_id TEXT NOT NULL,
                    source     TEXT NOT NULL,
                    enabled    INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (project_id, source)
                )
                """
            )
            await db.commit()

    async def set_enabled(self, project_id: str, source: str, enabled: bool) -> None:
        """Upsert the enabled flag for one (project, source) pair."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO document_state (project_id, source, enabled)
                VALUES (?, ?, ?)
                ON CONFLICT (project_id, source)
                DO UPDATE SET enabled = excluded.enabled
                """,
                (project_id, source, int(enabled)),
            )
            await db.commit()

    async def get_disabled_sources(self, project_id: str) -> set[str]:
        """Return the set of source labels toggled off for this project."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT source FROM document_state WHERE project_id = ? AND enabled = 0",
                (project_id,),
            )
            rows = await cursor.fetchall()
        return {r[0] for r in rows}

    async def get_enabled_map(self, project_id: str) -> dict[str, bool]:
        """Return {source: enabled} for every source with a stored row.

        Sources with no row are enabled by default — callers should treat a
        missing key as True.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT source, enabled FROM document_state WHERE project_id = ?",
                (project_id,),
            )
            rows = await cursor.fetchall()
        return {r[0]: bool(r[1]) for r in rows}

    async def delete_by_source(self, project_id: str, source: str) -> None:
        """Remove stored state for one source (called when it is deleted)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM document_state WHERE project_id = ? AND source = ?",
                (project_id, source),
            )
            await db.commit()

    async def delete_by_project(self, project_id: str) -> None:
        """Remove all stored state for a project (called on project delete)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM document_state WHERE project_id = ?", (project_id,)
            )
            await db.commit()

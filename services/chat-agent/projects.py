# SQLite-backed project store.
#
# Why projects?
#   history and a single document corpus.  Useful for a demo, but real
#   knowledge workers juggle multiple workstreams (Project Alpha, Project
#   Beta, Project Gamma).  Dumping everything into one brain means:
#     - Answers for Alpha get polluted by unrelated Beta context.
#     - You cannot delete one workstream without wiping everything.
#     - The agent cannot tell you what's happening in one project vs another.
#
#   A "project" here is a small container: a display name and a stable id.
#   Every message, vector, and ingested document is tagged with the
#   project's id so searches stay scoped.
#
# Why a schema_version table?
#   for project_id, which would break every query.  Recording the schema
#   version lets startup code detect the mismatch and wipe-and-recreate.
#   (Per owner decision: we wipe existing data rather than migrate.)

import uuid
from dataclasses import dataclass

import aiosqlite

# Bump this whenever the SQL schema changes in an incompatible way.
SCHEMA_VERSION = 2


@dataclass
class Project:
    """One project — the container that owns a slice of memory + documents."""
    id: str
    name: str
    created_at: str


class ProjectStore:
    """CRUD for projects + the `schema_version` sentinel table.

    Lives next to ConversationStore in the same SQLite file, so starting up
    the service creates both tables in one place (see main.py lifespan).
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the projects + schema_version tables if they do not exist.

        This is idempotent — safe on every startup.  The schema_version check
        is performed by the caller (main.py) so that any wipe-and-recreate
        logic stays out of this module.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id            TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    created_at    TEXT DEFAULT (datetime('now'))
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
                """
            )
            await db.commit()

    # Schema version helpers — used by main.py to decide whether to wipe
    async def current_version(self) -> int | None:
        """Return the recorded schema version, or None if never written."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT version FROM schema_version LIMIT 1")
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_version(self, version: int) -> None:
        """Record the given schema version (overwrites any existing row)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM schema_version")
            await db.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            await db.commit()

    # Project CRUD
    async def get_by_name(self, name: str) -> "Project | None":
        """Return a project whose name matches exactly (case-insensitive), or None."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id, name, created_at FROM projects WHERE LOWER(name) = LOWER(?)",
                (name,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return Project(
            id=row[0],
            name=row[1],
            created_at=row[2],
        )

    async def create(self, name: str) -> Project:
        """Insert a new project and return it.

        The id is a random UUID — stable, unique across machines, and safe to
        use in URLs and JSON without escaping.
        Raises ValueError if a project with the same name already exists.
        """
        if await self.get_by_name(name) is not None:
            raise ValueError(f"A project named {name!r} already exists.")
        project_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO projects (id, name) VALUES (?, ?)",
                (project_id, name),
            )
            await db.commit()
            # Fetch the server-assigned created_at so the returned object
            # matches what a later get() would return.
            cursor = await db.execute(
                "SELECT created_at FROM projects WHERE id = ?", (project_id,)
            )
            row = await cursor.fetchone()

        return Project(
            id=project_id,
            name=name,
            created_at=row[0] if row else "",
        )

    async def list(self) -> list[Project]:
        """Return all projects, newest first.

        Newest-first matches how humans think about active work — the project
        you created most recently is usually the one you're working on now.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, name, created_at
                FROM projects
                ORDER BY created_at DESC
                """
            )
            rows = await cursor.fetchall()

        return [
            Project(
                id=row[0],
                name=row[1],
                created_at=row[2],
            )
            for row in rows
        ]

    async def get(self, project_id: str) -> Project | None:
        """Return one project by id, or None if it does not exist.

        Used by the API layer to validate that an incoming `project_id`
        points at a real project before scoping any read/write against it.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, name, created_at
                FROM projects
                WHERE id = ?
                """,
                (project_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None
        return Project(
            id=row[0],
            name=row[1],
            created_at=row[2],
        )

    async def update(
        self,
        project_id: str,
        name: str | None = None,
    ) -> Project | None:
        """Update a project's name.

        Returns the updated project, or None if the id does not exist.
        Unset kwargs are left untouched — this is a partial update, not a
        full replacement.
        """
        existing = await self.get(project_id)
        if existing is None:
            return None

        new_name = name if name is not None else existing.name

        # Only check for duplicate names when the name is actually changing.
        if new_name.lower() != existing.name.lower():
            conflict = await self.get_by_name(new_name)
            if conflict is not None:
                raise ValueError(f"A project named {new_name!r} already exists.")

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE projects
                SET name = ?
                WHERE id = ?
                """,
                (new_name, project_id),
            )
            await db.commit()

        return Project(
            id=project_id,
            name=new_name,
            created_at=existing.created_at,
        )

    async def delete(self, project_id: str) -> bool:
        """Delete a project and cascade into its messages.

        Cascade scope:
          - projects row (this table)
          - messages rows with the same project_id (SQLite-side cascade)

        What this does NOT do:
          - Delete Qdrant vectors.  The API layer (main.py) handles that
            separately because this module has no knowledge of Qdrant.
            Keeping ProjectStore SQLite-only makes it trivial to unit-test.

        Returns True if a project was deleted, False if the id was unknown.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM projects WHERE id = ?", (project_id,)
            )
            deleted_projects = cursor.rowcount
            # Cascade: drop any messages tagged with this project.
            # Safe even if the project_id was never used.
            await db.execute(
                "DELETE FROM messages WHERE project_id = ?", (project_id,)
            )
            await db.commit()

        return deleted_projects > 0

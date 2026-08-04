# entity.py — semantic memory: structured per-entity tracking (people,
# stakeholders, tickets, organizations) so research findings tie back to a
# consistent identity instead of scattered citations across doc chunks.
#
# Extraction is independent of transcript.py's decisions/action_items/risks
# extraction: entities come from both transcripts and plain documents, while
# decisions/action_items/risks only ever come from transcripts. Sharing one
# prompt/schema would force plain document ingest to also run extraction it
# doesn't need, so entity extraction gets its own prompt and its own LLM
# call, reusing transcript._parse_extraction_json for the JSON-fence
# stripping since the parsing concern is identical.
#
# Merge policy: one row per (project_id, name, type). Re-extraction from a
# new source unions into `sources` and overwrites `attributes` with the
# freshest non-empty text (last-write-wins prose) rather than keeping a
# per-source history — the point of entity memory is a consistent identity,
# not a scattered log.

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite
from transcript import ChatFn, _parse_extraction_json

# Cap on how many entities find_matching() folds into a single /chat prompt.
# Local constant, not a Settings field — matches workflow.py's find_matching,
# which also has no configurable limit.
_MATCH_LIMIT = 5


@dataclass
class Entity:
    id: str
    project_id: str
    name: str
    type: str
    attributes: str | None
    sources: str  # JSON-encoded list[str]
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Extraction prompt — entity-only, deliberately narrower than transcript.py's
# EXTRACTION_SYSTEM_PROMPT so it can run against any document, not just
# meeting transcripts.
# ---------------------------------------------------------------------------
ENTITY_EXTRACTION_PROMPT = (
    "You extract named entities from text for a research knowledge base.\n"
    "Return ONLY a JSON object with exactly one key: \"entities\". No prose, "
    "no markdown fence, no commentary.\n\n"
    "Schema:\n"
    "{\n"
    '  "entities": [{"name": "...", "type": "...", "attributes": "...|null"}]\n'
    "}\n\n"
    "Rules:\n"
    "1. An entity is a specific person, stakeholder, organization, or ticket/"
    "   issue key (e.g. KAN-8) named in the text — not a generic topic.\n"
    "2. type is a short lowercase label: person, stakeholder, organization, "
    "   ticket, or other.\n"
    "3. attributes is a short free-text note capturing role/status/context "
    "   for this entity as it appears in the text, or null if nothing beyond "
    "   the name is known.\n"
    "4. Deduplicate — one entry per distinct entity, even if named multiple "
    "   times in the text.\n"
    "5. Empty list is fine if no entities are found. Always include the "
    "   \"entities\" key.\n"
    "6. Output must be valid JSON parseable by Python's json.loads."
)


async def extract_entities(text: str, chat_fn: ChatFn) -> list[dict]:
    """Call the LLM and return a list of {"name", "type", "attributes"} dicts.

    Caller is responsible for upserting into EntityStore.
    """
    messages = [
        {"role": "system", "content": ENTITY_EXTRACTION_PROMPT},
        {"role": "user", "content": text},
    ]
    raw_reply = await chat_fn(messages)
    parsed = _parse_extraction_json(raw_reply)
    return list(parsed.get("entities") or [])


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
class EntityStore:
    """SQLite-backed store for the entities table.

    Project-scoped only, no session_id — an entity extracted from one
    session's transcript should be recognized in another session's chat,
    which is the whole point of "consistent entities."
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the entities table + indexes if they do not yet exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    attributes TEXT,
                    sources TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_identity "
                "ON entities(project_id, name, type)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_project "
                "ON entities(project_id)"
            )
            await db.commit()

    # -- write -------------------------------------------------------------
    async def upsert_entity(
        self,
        project_id: str,
        name: str,
        entity_type: str,
        attributes: str | None,
        source: str,
    ) -> Entity:
        """Create or merge an entity identified by (project_id, name, type).

        On a repeat sighting: union `source` into the stored sources list
        (deduped), overwrite `attributes` only if the new text is non-empty,
        bump updated_at. Otherwise insert a new row with sources=[source].
        """
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, attributes, sources, created_at FROM entities "
                "WHERE project_id = ? AND name = ? AND type = ?",
                (project_id, name, entity_type),
            ) as cur:
                row = await cur.fetchone()

            if row is None:
                entity_id = str(uuid.uuid4())
                sources_list = [source]
                await db.execute(
                    "INSERT INTO entities (id, project_id, name, type, "
                    "attributes, sources, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entity_id, project_id, name, entity_type, attributes,
                        json.dumps(sources_list), now, now,
                    ),
                )
                await db.commit()
                return Entity(
                    id=entity_id,
                    project_id=project_id,
                    name=name,
                    type=entity_type,
                    attributes=attributes,
                    sources=json.dumps(sources_list),
                    created_at=now,
                    updated_at=now,
                )

            entity_id, existing_attributes, existing_sources_json, created_at = row
            sources_list = json.loads(existing_sources_json)
            if source not in sources_list:
                sources_list.append(source)
            new_attributes = attributes if (attributes or "").strip() else existing_attributes

            await db.execute(
                "UPDATE entities SET attributes = ?, sources = ?, updated_at = ? "
                "WHERE id = ?",
                (new_attributes, json.dumps(sources_list), now, entity_id),
            )
            await db.commit()

            return Entity(
                id=entity_id,
                project_id=project_id,
                name=name,
                type=entity_type,
                attributes=new_attributes,
                sources=json.dumps(sources_list),
                created_at=created_at,
                updated_at=now,
            )

    # -- read ----------------------------------------------------------------
    async def get_by_name(self, project_id: str, name: str, entity_type: str) -> Entity | None:
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT id, project_id, name, type, attributes, sources, "
            "created_at, updated_at FROM entities "
            "WHERE project_id = ? AND name = ? AND type = ?",
            (project_id, name, entity_type),
        ) as cur:
            row = await cur.fetchone()
        return Entity(*row) if row else None

    async def list_by_project(self, project_id: str) -> list[Entity]:
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT id, project_id, name, type, attributes, sources, "
            "created_at, updated_at FROM entities "
            "WHERE project_id = ? ORDER BY updated_at DESC",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [Entity(*r) for r in rows]

    async def find_matching(self, project_id: str, message: str) -> list[Entity]:
        """v1 heuristic: case-insensitive substring match of entity name
        against the message. Returns every hit, capped at _MATCH_LIMIT,
        most-recently-updated first.

        Same deliberately-simple approach as WorkflowStore.find_matching —
        semantic matching is future work.
        """
        message_lower = message.lower()
        hits = [
            e for e in await self.list_by_project(project_id)
            if e.name.lower() in message_lower
        ]
        return hits[:_MATCH_LIMIT]

    # -- delete ----------------------------------------------------------------
    async def delete_by_project(self, project_id: str) -> None:
        """Remove all entities for a project (called on project delete)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM entities WHERE project_id = ?", (project_id,))
            await db.commit()

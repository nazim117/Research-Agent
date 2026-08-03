# semantic_cache.py — cache for embeddings.py's embed() calls.
#
# Scope (see MEMORY_PLAN.md, priority #4): embeddings only, not LLM replies.
# embed(text) is a pure deterministic function of its exact input string (TEI
# server, fixed model), so caching it by exact-normalized-text hash is
# provably safe. Caching llm.chat() output would not be — its reply depends
# on the full assembled prompt (RAG chunks, history), which differs turn to
# turn even for a "similar" question.
#
# Concrete win found in this repo: main.py's /chat handler embeds
# req.message twice per turn (once directly for conversation-memory search,
# once again inside rag.retrieve() for document search) — same exact
# string, two network round-trips. Routing both through embed_cached()
# turns the second into a guaranteed cache hit with no call-site refactor.
#
# Cache key normalization is limited to stripping leading/trailing
# whitespace — no casefolding or internal-whitespace collapsing, since that
# could conflate strings that legitimately embed differently.

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from embeddings import embed


@dataclass
class CacheStats:
    entry_count: int
    total_hits: int


class SemanticCacheStore:
    """Manages the embedding_cache table.

    Lives in the same SQLite file as everything else, following the same
    pattern as WorkflowStore/ToolboxStore/ScratchpadStore.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the table + unique index if they do not exist. Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    vector TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_hit_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_embedding_cache_key "
                "ON embedding_cache(project_id, text_hash)"
            )
            await db.commit()

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

    async def get(self, project_id: str, text: str) -> list[float] | None:
        """Look up a cached vector. Bumps hit_count/last_hit_at on hit."""
        text_hash = self._hash(text)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, vector FROM embedding_cache "
                "WHERE project_id = ? AND text_hash = ?",
                (project_id, text_hash),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            entry_id, vector_json = row
            await db.execute(
                "UPDATE embedding_cache SET hit_count = hit_count + 1, "
                "last_hit_at = ? WHERE id = ?",
                (now, entry_id),
            )
            await db.commit()
        return json.loads(vector_json)

    async def set(self, project_id: str, text: str, vector: list[float]) -> None:
        """Insert a new cache entry. No-op if the key already exists (a
        concurrent race lost to another writer keeps the first value)."""
        text_hash = self._hash(text)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO embedding_cache "
                "(id, project_id, text_hash, vector, created_at, last_hit_at, hit_count) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (str(uuid.uuid4()), project_id, text_hash, json.dumps(vector), now, now),
            )
            await db.commit()

    async def get_stats(self, project_id: str) -> CacheStats:
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT COUNT(*), SUM(hit_count) FROM embedding_cache WHERE project_id = ?",
            (project_id,),
        ) as cur:
            row = await cur.fetchone()
        entry_count, total_hits = row
        return CacheStats(entry_count=entry_count or 0, total_hits=int(total_hits or 0))

    async def delete_by_project(self, project_id: str) -> None:
        """Remove all cache entries for a project (project delete cascade)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM embedding_cache WHERE project_id = ?", (project_id,)
            )
            await db.commit()


async def embed_cached(cache: SemanticCacheStore, project_id: str, text: str) -> list[float]:
    """embed(), but through the cache: on miss, call embed() and store the result."""
    cached = await cache.get(project_id, text)
    if cached is not None:
        return cached
    vector = await embed(text)
    await cache.set(project_id, text, vector)
    return vector

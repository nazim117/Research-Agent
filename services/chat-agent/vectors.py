# vectors.py — Qdrant vector database client.
#
# What is Qdrant?
#   Qdrant is a vector database: a database optimised for storing and searching
#   high-dimensional vectors.  A regular database like SQLite can find rows WHERE
#   content = 'hello' (exact match).  Qdrant finds rows WHERE vector ≈ query_vector
#   (similarity match) — it returns the most semantically similar stored items.
#
# What is cosine similarity?
#   The similarity score Qdrant returns is cosine similarity: a number between
#   -1 and 1 that measures the angle between two vectors.
#   - 1.0  → identical direction  → semantically the same text
#   - 0.0  → perpendicular        → unrelated text
#   - -1.0 → opposite direction   → antonyms (rare in practice)
#   In practice, scores above ~0.85 are very similar; below ~0.5 are unrelated.
#
# Data model:
#   Each stored "point" in Qdrant has three parts:
#   - id:      a UUID string that uniquely identifies this point
#   - vector:  the 768-float embedding of the text
#   - payload: a JSON dict that ALWAYS includes project_id (the partition key)
#              alongside domain fields (session_id/role/content for conversation
#              points, source/chunk_index/text for document points).
#
# Why "single collection + payload filter" instead of "collection per project"?
#   Two obvious designs exist for isolating projects in Qdrant:
#     a) Create a fresh collection per project (e.g. conversations_alpha,
#        conversations_beta).  Strong isolation, but collection creation/delete
#        is expensive and the number of collections grows with projects.
#     b) Keep one "conversations" collection; tag each point with project_id
#        in its payload; filter every search by that tag.
#   We go with (b).  It scales well at our expected size (tens of projects,
#   millions of points), and Qdrant's payload indexes make filtered search
#   nearly as fast as unfiltered.  See ensure_collection() for the index setup.

from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import HTTPException
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
    FilterSelector,
)

from config import settings


@dataclass
class Hit:
    """One result from a Qdrant similarity search."""
    score: float             # cosine similarity score (higher = more similar)
    session_id: str          # which conversation this message belongs to
    role: str                # 'user' or 'assistant'
    content: str             # the original message text
    # Raw Qdrant payload — contains all stored fields including project_id.
    # Conversation hits have {project_id, session_id, role, content};
    # document hits have {project_id, source, chunk_index, text}.
    # Callers that need those domain fields (rag.py) read from this dict.
    payload: dict = field(default_factory=dict)


# Key name used consistently across the whole service for the project partition.
# Hard-coded here on purpose — renaming it requires a Qdrant migration, so it
# should stay constant and searchable via grep.
PROJECT_ID_KEY = "project_id"


class VectorStore:
    """Thin async wrapper around Qdrant for storing and searching vectors.

    All reads and writes are scoped by a `project_id` argument.  The store
    itself is collection-agnostic — main.py and rag.py decide which collection
    to use (`conversations` vs. `documents`).
    """

    def __init__(self, url: str) -> None:
        # AsyncQdrantClient holds the connection config but does not connect
        # until the first actual call — no connection at construction time.
        self._client = AsyncQdrantClient(url=url)

    async def ensure_collection(self, name: str, dim: int) -> None:
        """Create a Qdrant collection (and its project_id payload index) if missing.

        Args:
            name: Collection name (e.g. "conversations").
            dim:  Vector dimension — must match the embedding model output.
                  nomic-embed-text produces 768-dimensional vectors.

        Idempotent: calling on an existing collection is a no-op apart from
        re-asserting the payload index (which Qdrant also treats as no-op
        when the index already exists).

        Why the payload index?  Qdrant filters over a non-indexed payload
        field work correctly but scan every candidate point.  Indexing
        `project_id` lets Qdrant skip directly to the matching subset —
        roughly the same speedup a B-tree index gives a SQL WHERE clause.
        """
        try:
            exists = await self._client.collection_exists(name)
            if not exists:
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(
                        size=dim,
                        # COSINE measures the angle between vectors.
                        # It is the right choice for text embeddings because
                        # the *direction* of a vector carries the meaning,
                        # not its magnitude (length).
                        distance=Distance.COSINE,
                    ),
                )

            # Create the payload index.  Safe to call every startup —
            # Qdrant returns success if the index already exists.
            await self._client.create_payload_index(
                collection_name=name,
                field_name=PROJECT_ID_KEY,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not connect to Qdrant at {settings.qdrant_url}: {exc}",
            ) from exc

    async def reset_collection(self, name: str, dim: int) -> None:
        """Drop and recreate a collection from scratch.

        Called by startup code when schema_version mismatches. An existing collection's
        points have no project_id tag and would otherwise be invisible to
        every filtered search.
        """
        try:
            if await self._client.collection_exists(name):
                await self._client.delete_collection(name)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant reset_collection failed: {exc}",
            ) from exc
        await self.ensure_collection(name, dim)

    async def upsert(
        self,
        collection: str,
        project_id: str,
        vector: list[float],
        payload: dict,
        id: str | None = None,
    ) -> str:
        """Store one vector with its payload, tagged with project_id.

        Args:
            collection: Target collection name.
            project_id: The partition this point belongs to.  Injected into
                        the stored payload as `project_id` (overwriting any
                        caller-provided value, on purpose).
            vector:     The embedding vector (must match collection dimension).
            payload:    Domain fields for this point (e.g. session_id/role/content
                        for conversations, source/chunk_index/text for documents).
            id:         Optional UUID string.  A new UUID is generated if omitted.

        Returns:
            The point ID (useful if the caller wants to update the point later).
        """
        point_id = id or str(uuid4())
        # Copy-then-overwrite so callers can't accidentally ship a point with
        # no project_id, and can't override it by passing one in `payload`.
        stored_payload = dict(payload)
        stored_payload[PROJECT_ID_KEY] = project_id

        try:
            await self._client.upsert(
                collection_name=collection,
                points=[PointStruct(id=point_id, vector=vector, payload=stored_payload)],
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant upsert failed: {exc}",
            ) from exc

        return point_id

    async def search(
        self,
        collection: str,
        project_id: str,
        vector: list[float],
        k: int = 5,
    ) -> list[Hit]:
        """Find the k most similar vectors within one project.

        Args:
            collection: Collection to search.
            project_id: Partition to restrict the search to.  Points tagged
                        with any other project_id are invisible.
            vector:     The query embedding (same dimension as stored vectors).
            k:          Number of results to return.

        Returns:
            List of Hit objects ordered by score descending (most similar first).
        """
        try:
            results = await self._client.search(
                collection_name=collection,
                query_vector=vector,
                limit=k,
                with_payload=True,   # include the stored payload in results
                # The filter is the whole point of this method:
                # Qdrant will only consider points where project_id matches.
                query_filter=_project_filter(project_id),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant search failed: {exc}",
            ) from exc

        return [
            Hit(
                score=r.score,
                session_id=r.payload.get("session_id", ""),
                role=r.payload.get("role", ""),
                content=r.payload.get("content", ""),
                payload=dict(r.payload),  # full payload for callers that need it
            )
            for r in results
        ]

    async def delete_by_project(self, collection: str, project_id: str) -> None:
        """Remove every point in `collection` tagged with the given project_id.

        Used by the API layer when a project is deleted — cascades vectors
        out of Qdrant to match the SQLite-side cascade in ProjectStore.delete().
        """
        try:
            await self._client.delete(
                collection_name=collection,
                points_selector=_project_filter(project_id),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant delete_by_project failed: {exc}",
            ) from exc

    async def scroll_payloads(
        self, collection: str, project_id: str
    ) -> list[dict]:
        """Return all payload dicts for a project by scrolling without a query vector.

        Used by rag.list_sources to count distinct sources without embedding a query.
        Pages through the collection until Qdrant returns next_offset=None.
        """
        payloads: list[dict] = []
        offset = None
        try:
            while True:
                results, next_offset = await self._client.scroll(
                    collection_name=collection,
                    scroll_filter=_project_filter(project_id),
                    with_payload=True,
                    with_vectors=False,
                    limit=100,
                    offset=offset,
                )
                payloads.extend(r.payload for r in results)
                if next_offset is None:
                    break
                offset = next_offset
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant scroll failed: {exc}",
            ) from exc
        return payloads

    async def fetch_by_source(
        self, collection: str, project_id: str, source: str
    ) -> list[Hit]:
        """Return all stored chunks for a specific source label within one project.

        Used when the user explicitly names a ticket (e.g. "KAN-8") so we can
        inject its content regardless of semantic similarity score.
        """
        payloads: list[dict] = []
        offset = None
        src_filter = Filter(
            must=[
                FieldCondition(key=PROJECT_ID_KEY, match=MatchValue(value=project_id)),
                FieldCondition(key="source", match=MatchValue(value=source)),
            ]
        )
        try:
            while True:
                results, next_offset = await self._client.scroll(
                    collection_name=collection,
                    scroll_filter=src_filter,
                    with_payload=True,
                    with_vectors=False,
                    limit=100,
                    offset=offset,
                )
                payloads.extend(r.payload for r in results)
                if next_offset is None:
                    break
                offset = next_offset
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant fetch_by_source failed: {exc}",
            ) from exc
        return [
            Hit(
                score=1.0,
                session_id="",
                role="",
                content=p.get("text", ""),
                payload=dict(p),
            )
            for p in payloads
        ]

    async def delete_by_source(
        self, collection: str, project_id: str, source: str
    ) -> None:
        """Remove all points for a specific source document within one project.

        Called by sync.py before re-ingesting a PM item so that re-syncing
        the same ticket doesn't stack duplicate vectors.  Also handles the
        "ticket was edited and now has fewer chunks" case — old trailing
        chunks disappear cleanly because we delete-then-rewrite, not append.

        Args:
            collection: Usually settings.qdrant_docs_collection ("documents").
            project_id: Restrict deletion to this project's points only.
            source:     The source label used at ingest time (e.g. "jira:KAN-1").
        """
        try:
            await self._client.delete(
                collection_name=collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key=PROJECT_ID_KEY,
                                match=MatchValue(value=project_id),
                            ),
                            FieldCondition(
                                key="source",
                                match=MatchValue(value=source),
                            ),
                        ]
                    )
                ),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant delete_by_source failed: {exc}",
            ) from exc


def _project_filter(project_id: str) -> Filter:
    """Build a Qdrant Filter matching points with the given project_id.

    Kept as a module-level helper so search() and delete_by_project() build
    the filter in exactly the same way.  One definition, one chance to get
    it wrong.
    """
    return Filter(
        must=[
            FieldCondition(
                key=PROJECT_ID_KEY,
                match=MatchValue(value=project_id),
            )
        ]
    )

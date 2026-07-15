# RAG (Retrieval-Augmented Generation) ingest and retrieve pipeline.
#
# What is RAG?
#   RAG is a pattern for giving an LLM access to private knowledge it was not
#   trained on.  Instead of fine-tuning the model (expensive), we:
#     1. Split the document into chunks.
#     2. Embed each chunk as a vector (using Ollama).
#     3. Store the vectors in Qdrant.
#     4. At query time, embed the user's question and search for the most similar
#        chunks.
#     5. Inject those chunks into the prompt so the LLM can read them.
#
#   The LLM never "learns" the document; it just reads the relevant excerpts on
#   every request.  This is slower than fine-tuning but much cheaper and allows
#   the knowledge base to be updated without retraining.
#
# Why chunk instead of embedding the whole document?
#   nomic-embed-text accepts up to ~512 tokens (~400 words).  A longer input is
#   truncated.  More importantly, a 10-page document embedded as one vector loses
#   fine-grained meaning: a query about paragraph 7 would score poorly against
#   the whole-doc vector.  Smaller chunks produce more precise matches.
#
# Why overlap?
#   If a sentence is split across two chunk boundaries, both chunks get a partial
#   sentence that may not make sense alone.  Overlapping by ~50 characters means
#   each chunk repeats the tail of its predecessor, so split sentences stay
#   findable.
#
# Collection used: settings.qdrant_docs_collection ("documents")
# This is separate from the "conversations" collection so document search and
# conversation memory search never interfere with each other.

import logging
from dataclasses import dataclass

from config import settings
from embeddings import embed
from vectors import VectorStore

logger = logging.getLogger("uvicorn.error")


@dataclass
class SourceSummary:
    """Distinct source label with chunk count — returned by list_sources."""
    source: str
    chunks: int


@dataclass
class Chunk:
    """One retrieved document chunk from the RAG pipeline."""
    score: float        # cosine similarity to the query (higher = more relevant)
    source: str         # the label passed to ingest() — usually a filename or URL
    chunk_index: int    # position of this chunk in the original document (0-based)
    text: str           # the chunk text itself


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks of roughly `size` characters.

    Args:
        text:    The full document text to split.
        size:    Maximum characters per chunk.
        overlap: How many characters each chunk shares with the previous one.

    Returns:
        List of text chunks.  The last chunk may be shorter than `size`.

    Example with size=10, overlap=3:
        "abcdefghijklmno"
        → ["abcdefghij", "hijklmnopq", ...]
                          ^^^  overlap
    """
    if not text:
        return []

    chunks = []
    start = 0
    step = size - overlap

    while start < len(text):
        chunks.append(text[start : start + size])
        start += step

    return chunks


async def ingest(
    project_id: str,
    source: str,
    text: str,
    vstore: VectorStore,
) -> int:
    """Split a document into chunks, embed each one, and store them in Qdrant
    tagged with `project_id`.

    Args:
        project_id: The project this document belongs to.  Every resulting
                    chunk's Qdrant payload is tagged with this id so it only
                    comes back in searches scoped to the same project.
        source:     A label for this document — typically a filename or URL.
                    Stored alongside project_id so retrieved chunks can be
                    attributed back to their origin.
        text:       The full document text.
        vstore:     The VectorStore instance to write into.

    Returns:
        The number of chunks stored.  Useful for confirming how much was indexed.
    """
    chunks = chunk_text(text)

    for index, chunk in enumerate(chunks):
        vector = await embed(chunk)
        await vstore.upsert(
            collection=settings.qdrant_docs_collection,
            project_id=project_id,
            vector=vector,
            payload={
                "source": source,
                "chunk_index": index,
                "text": chunk,
            },
        )

    logger.info(
        "Ingested %d chunks from source %r into project %r",
        len(chunks),
        source,
        project_id,
    )
    return len(chunks)


async def list_sources(
    project_id: str,
    vstore: VectorStore,
) -> list[SourceSummary]:
    """Return distinct sources ingested into a project, with their chunk counts.

    Scrolls the documents collection without a query vector, so no embedding
    cost. Aggregates by payload.source — the label supplied at ingest time.
    """
    payloads = await vstore.scroll_payloads(
        collection=settings.qdrant_docs_collection,
        project_id=project_id,
    )
    counts: dict[str, int] = {}
    for p in payloads:
        src = p.get("source", "")
        counts[src] = counts.get(src, 0) + 1
    return [SourceSummary(source=s, chunks=c) for s, c in sorted(counts.items())]


async def retrieve_by_source(
    project_id: str,
    source: str,
    vstore: VectorStore,
) -> list[Chunk]:
    """Return all stored chunks for an exact source label (e.g. 'jira:KAN-8').

    Used when the user explicitly references a ticket key so the LLM always
    gets that ticket's content regardless of semantic similarity.
    """
    hits = await vstore.fetch_by_source(
        collection=settings.qdrant_docs_collection,
        project_id=project_id,
        source=source,
    )
    return [
        Chunk(
            score=h.score,
            source=h.payload.get("source", ""),
            chunk_index=h.payload.get("chunk_index", 0),
            text=h.payload.get("text", ""),
        )
        for h in hits
    ]


async def retrieve(
    project_id: str,
    query: str,
    k: int,
    vstore: VectorStore,
    score_threshold: float | None = None,
) -> list[Chunk]:
    """Find the k document chunks most semantically similar to `query`,
    restricted to one project.

    Args:
        project_id:      Only chunks tagged with this id are considered.
                          Chunks from other projects are invisible.
        query:           The search string — usually the user's current message.
        k:               How many chunks to return.
        vstore:          The VectorStore instance to search.
        score_threshold: Minimum cosine similarity a chunk must have to be
                          returned.  None (default) means no cutoff.

    Returns:
        List of Chunk objects ordered by score descending (most relevant first).
    """
    query_vec = await embed(query)
    hits = await vstore.search(
        collection=settings.qdrant_docs_collection,
        project_id=project_id,
        vector=query_vec,
        k=k,
        score_threshold=score_threshold,
    )

    # Map VectorStore Hit objects to Chunk objects using the raw payload dict.
    # The payload fields were set by ingest(): {project_id, source, chunk_index, text}.
    return [
        Chunk(
            score=h.score,
            source=h.payload.get("source", ""),
            chunk_index=h.payload.get("chunk_index", 0),
            text=h.payload.get("text", ""),
        )
        for h in hits
    ]

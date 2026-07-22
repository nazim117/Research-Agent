# tests/test_rag_integration.py — real Qdrant + embeddings-service RAG pipeline tests.
#
# These tests call rag.ingest() and rag.retrieve() against a live Qdrant and
# the bundled embeddings service (TEI, not Ollama — see embeddings.py).
# Skipped when either is unreachable (conftest.qdrant_up + embeddings_up).
#
# What is covered (the "rag.py gap" from docs/test-suite-analysis.md §5.2):
#   - ingest() chunks a document and stores vectors in Qdrant
#   - retrieve() returns chunks with the correct source label
#   - a document ingested under project A is NOT returned for project B
#   - retrieve() returns no results when nothing has been ingested

import uuid

import pytest

from config import settings

pytestmark = pytest.mark.integration


# Fixture that requires BOTH real Qdrant and the real embeddings service.
@pytest.fixture
def both_up(qdrant_up, embeddings_up):
    """Composite fixture: skip if either Qdrant or the embeddings service is unreachable."""


SAMPLE_TEXT = (
    "The board approved the Q3 roadmap. "
    "Key decisions: ship v2 by August, hire two backend engineers, "
    "and defer the mobile app to Q4. "
    "Action items: CEO to announce roadmap, CTO to open job reqs."
)


# ─── ingest ──────────────────────────────────────────────────────────────────

async def test_ingest_returns_chunk_count(real_vstore, unique_project, both_up):
    """ingest() returns a positive chunk count for a non-trivial document."""
    from rag import ingest

    chunk_count = await ingest(
        project_id=unique_project,
        source="test:sample-doc",
        text=SAMPLE_TEXT,
        vstore=real_vstore,
    )

    assert isinstance(chunk_count, int)
    assert chunk_count >= 1, "at least one chunk expected"


async def test_ingest_stores_queryable_chunks(real_vstore, unique_project, both_up):
    """After ingest, retrieve() returns chunks with the correct source label."""
    from rag import ingest, retrieve

    source = "test:searchable-doc"
    await ingest(
        project_id=unique_project,
        source=source,
        text=SAMPLE_TEXT,
        vstore=real_vstore,
    )

    # Query using a phrase that appears in the sample text.
    chunks = await retrieve(
        project_id=unique_project,
        query="Q3 roadmap decisions",
        k=5,
        vstore=real_vstore,
    )

    assert len(chunks) >= 1, "retrieve() should return at least one chunk after ingest"
    sources = {c.source for c in chunks}
    assert source in sources, f"expected source '{source}' in results, got {sources}"


# ─── cross-project isolation ─────────────────────────────────────────────────

async def test_rag_does_not_bleed_across_projects(real_vstore, unique_project, both_up):
    """Chunks ingested under project A are NOT returned for project B.

    This is the critical correctness invariant for the multi-project isolation
    design (see CLAUDE.md — Target architecture / Isolation strategy).
    """
    from rag import ingest, retrieve

    project_a = unique_project
    project_b = str(uuid.uuid4())

    source_a = "test:project-a-only"
    await ingest(
        project_id=project_a,
        source=source_a,
        text=SAMPLE_TEXT,
        vstore=real_vstore,
    )

    try:
        # Searching under project B should not surface project A's data.
        chunks = await retrieve(
            project_id=project_b,
            query="Q3 roadmap decisions",
            k=5,
            vstore=real_vstore,
        )
        sources = {c.source for c in chunks}
        assert source_a not in sources, (
            f"project A source '{source_a}' leaked into project B results"
        )
    finally:
        await real_vstore.delete_by_project(
            settings.qdrant_docs_collection, project_b
        )


# ─── retrieve on empty store ──────────────────────────────────────────────────

async def test_retrieve_returns_empty_when_nothing_ingested(real_vstore, unique_project, both_up):
    """retrieve() returns an empty list for a project with no ingested documents."""
    from rag import retrieve

    chunks = await retrieve(
        project_id=unique_project,
        query="anything",
        k=5,
        vstore=real_vstore,
    )
    assert chunks == []

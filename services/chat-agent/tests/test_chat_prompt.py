# test_chat_prompt.py — seam test for the RAG → LLM prompt-construction path.
#
# The bug this test guards against: retrieved document chunks reach the RAG
# layer correctly but then disappear (or arrive with weak phrasing) in the
# messages list that is actually sent to DeepSeek.  That exact failure caused
# DeepSeek to claim "no access to your PM system" on 2026-04-18 even though
# sync and retrieval both worked.
#
# Approach: call the real /chat route with a real FastAPI test client but
# replace `llm.chat` with a spy that records its `messages` argument and
# returns a fixed string.  Qdrant / Ollama / SQLite are NOT required — we
# patch the three external I/O functions (embed, rag.retrieve, chat) with
# fakes that return deterministic values.
#
# What this tests:
#   1. When rag.retrieve returns a chunk, the prompt contains a
#      "--- PROJECT KNOWLEDGE ---" block.
#   2. The source label (e.g. "source:DOC-1") appears in that block.
#   3. The system message uses directive language ("authoritative") and
#      not the old tentative phrasing ("may be relevant").
#   4. Prior assistant refusals injected via recent history are stripped.

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# We import the app *after* all patches are in place in each test, so use
# a late import inside the fixtures instead of a module-level import.


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FAKE_PROJECT_ID = "proj-test-1234"
FAKE_SESSION_ID = "sess-test-5678"
FAKE_EMBED_VEC  = [0.0] * 768


def _make_chunk(source: str = "source:DOC-1", text: str = "Fix login bug — Status: In Progress"):
    """Return a rag.Chunk-like object."""
    from rag import Chunk
    return Chunk(score=0.9, source=source, chunk_index=0, text=text)


# ---------------------------------------------------------------------------
# Helpers — the fake implementations injected via patch
# ---------------------------------------------------------------------------

async def _fake_embed(_text: str) -> list[float]:
    return FAKE_EMBED_VEC


async def _fake_retrieve(_project_id, _query, k, vstore, score_threshold=None, exclude_sources=None):
    return [_make_chunk()]


async def _fake_retrieve_empty(_project_id, _query, k, vstore, score_threshold=None, exclude_sources=None):
    return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doc_chunk_produces_project_knowledge_block():
    """When rag.retrieve returns a chunk, the LLM receives a PROJECT KNOWLEDGE block."""
    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "Here is what I found: [source:DOC-1]"

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What tickets are in progress?",
            })

    assert resp.status_code == 200

    system_messages = [m for m in captured_messages if m["role"] == "system"]
    assert system_messages, "Expected at least one system message in the prompt"

    knowledge_block = next(
        (m for m in system_messages if "PROJECT KNOWLEDGE" in m["content"]),
        None,
    )
    assert knowledge_block is not None, (
        "Expected a '--- PROJECT KNOWLEDGE ---' block in the system messages.\n"
        f"System messages received: {[m['content'][:120] for m in system_messages]}"
    )
    assert "source:DOC-1" in knowledge_block["content"]
    assert "authoritative" in knowledge_block["content"].lower()
    # The chunk must be numbered — [1] prefix must appear in the block.
    assert "[1]" in knowledge_block["content"], (
        "Expected numbered reference [1] in the knowledge block"
    )


@pytest.mark.asyncio
async def test_no_doc_chunks_no_knowledge_block():
    """When rag.retrieve returns nothing, no PROJECT KNOWLEDGE block is injected."""
    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "I don't have that information."

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve_empty),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What tickets are in progress?",
            })

    assert resp.status_code == 200
    system_messages = [m for m in captured_messages if m["role"] == "system"]
    assert not any("PROJECT KNOWLEDGE" in m["content"] for m in system_messages)


@pytest.mark.asyncio
async def test_prior_refusals_stripped_from_recent_history():
    """Prior assistant refusals in recent history are filtered out of the prompt."""
    captured_messages = []

    refusal_turn = {"role": "assistant", "content": "I do not have access to your project management system."}
    good_turn    = {"role": "user",      "content": "What tickets are in progress?"}

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "KAN-1 is In Progress. [source:DOC-1]"

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[good_turn, refusal_turn]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What tickets are in progress?",
            })

    assert resp.status_code == 200
    # The refusal turn must not appear in the prompt sent to DeepSeek.
    assert not any(
        "i do not have access" in m["content"].lower()
        for m in captured_messages
    ), "Refusal from previous turn leaked into the prompt"
    # The good user turn must still be present.
    assert any(m["content"] == good_turn["content"] for m in captured_messages)


@pytest.mark.asyncio
async def test_source_label_in_prompt():
    """The source label 'source:DOC-1' appears verbatim in the knowledge block."""
    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "DOC-1 covers that. [source:DOC-1]"

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "Who is working on DOC-1?",
            })

    all_content = " ".join(m["content"] for m in captured_messages)
    assert "source:DOC-1" in all_content
    assert resp.status_code != 500


# ---------------------------------------------------------------------------
# Citation tests (Step 9)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_chunk_produces_citations_array():
    """When one chunk is retrieved, /chat returns citations=[{ref:1, source, chunk_index}]."""
    async def _spy_chat(messages):
        return "KAN-1 is in progress [1]."

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What is KAN-1?",
            })

    assert resp.status_code == 200
    data = resp.json()
    assert "citations" in data
    assert len(data["citations"]) == 1
    cit = data["citations"][0]
    assert cit["ref"] == 1
    assert cit["source"] == "source:DOC-1"
    assert cit["chunk_index"] == 0


@pytest.mark.asyncio
async def test_no_chunks_produces_empty_citations():
    """When rag.retrieve returns nothing, citations is an empty list."""
    async def _spy_chat(messages):
        return "I don't have that information."

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve_empty),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What happened last sprint?",
            })

    assert resp.status_code == 200
    data = resp.json()
    assert data["citations"] == []


@pytest.mark.asyncio
async def test_two_chunks_produce_ordered_citations():
    """Two distinct chunks produce refs [1] and [2] in the prompt and citations array."""
    from rag import Chunk
    chunk_a = Chunk(score=0.9, source="source:DOC-1", chunk_index=0, text="First chunk content")
    chunk_b = Chunk(score=0.8, source="notes.md", chunk_index=2, text="Second chunk content")

    async def _retrieve_two(_project_id, _query, k, vstore, score_threshold=None, exclude_sources=None):
        return [chunk_a, chunk_b]

    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "Answer draws on [1] and [2]."

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_retrieve_two),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "Summarise recent work.",
            })

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["citations"]) == 2
    assert data["citations"][0] == {"ref": 1, "source": "source:DOC-1", "chunk_index": 0}
    assert data["citations"][1] == {"ref": 2, "source": "notes.md", "chunk_index": 2}

    # Both [1] and [2] must appear in the knowledge block sent to the LLM.
    knowledge_block = next(
        (m for m in captured_messages if "PROJECT KNOWLEDGE" in m.get("content", "")),
        None,
    )
    assert knowledge_block is not None
    assert "[1]" in knowledge_block["content"]
    assert "[2]" in knowledge_block["content"]


# ---------------------------------------------------------------------------
# Web search tests — user-triggered (req.web_search), not model-triggered.
# See main.py's post_chat step 3d.
# ---------------------------------------------------------------------------

FAKE_WEB_RESULTS = {
    "results": [
        {"title": "Example result", "url": "https://example.com/a", "snippet": "About example A."},
    ]
}


@pytest.mark.asyncio
async def test_web_search_off_by_default_never_calls_mcp():
    """Omitting web_search (default False) must never call mcp.call at all."""
    async def _spy_chat(messages):
        return "answer"

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve_empty),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
        patch("main._mcp") as mcp,
    ):
        mcp.call = AsyncMock(return_value=FAKE_WEB_RESULTS)
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What's the weather today?",
            })

    assert resp.status_code == 200
    mcp.call.assert_not_called()
    assert resp.json()["citations"] == []


@pytest.mark.asyncio
async def test_web_search_on_injects_results_and_citations():
    """web_search=true calls mcp.call("web_search", ...) and folds results into
    both the prompt and the citations array, numbered after doc chunks.
    """
    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "Here's what I found [1]."

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve_empty),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
        patch("main._mcp") as mcp,
    ):
        mcp.call = AsyncMock(return_value=FAKE_WEB_RESULTS)
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What's the latest release of Qdrant?",
                "web_search": True,
            })

    assert resp.status_code == 200
    mcp.call.assert_awaited_once_with(
        "web_search",
        {"query": "What's the latest release of Qdrant?", "limit": 5},
        project_id=FAKE_PROJECT_ID,
    )

    data = resp.json()
    assert data["citations"] == [{"ref": 1, "source": "https://example.com/a", "chunk_index": 0}]

    web_block = next(
        (m for m in captured_messages if "WEB SEARCH RESULTS" in m.get("content", "")),
        None,
    )
    assert web_block is not None
    assert "https://example.com/a" in web_block["content"]
    assert "[1]" in web_block["content"]


@pytest.mark.asyncio
async def test_web_search_failure_degrades_gracefully():
    """If the search backend errors, the chat turn still succeeds — the LLM
    is told the search failed instead of the whole request 500ing.
    """
    from mcp_client import MCPError

    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "I couldn't search, but here's what I know."

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve_empty),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
        patch("main._mcp") as mcp,
    ):
        mcp.call = AsyncMock(side_effect=MCPError("search backend unreachable"))
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What's new in Qdrant?",
                "web_search": True,
            })

    assert resp.status_code == 200
    assert resp.json()["citations"] == []

    error_block = next(
        (m for m in captured_messages if "search failed" in m.get("content", "")),
        None,
    )
    assert error_block is not None
    assert "search backend unreachable" in error_block["content"]


# ---------------------------------------------------------------------------
# Workflow + toolbox-stats annotation tests
# ---------------------------------------------------------------------------

def _make_matched_workflow():
    from workflow import Workflow, WorkflowStep
    step = WorkflowStep(
        id="step-1", workflow_id="wf-1", step_order=0,
        tool_name="web_search", arguments='{"query": "PM status"}',
        description="search for status", created_at="x",
    )
    return Workflow(
        id="wf-1", project_id=FAKE_PROJECT_ID, name="weekly-briefing",
        trigger="briefing", description=None, created_at="x", steps=[step],
    )


@pytest.mark.asyncio
async def test_matched_workflow_annotated_with_success_rate():
    """A matched workflow step gets a '[n/m succeeded]' suffix when toolbox
    has logged history for that tool.
    """
    from toolbox import ToolStats

    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "Here's the briefing."

    stats = ToolStats(
        tool_name="web_search", call_count=8, success_count=7, failure_count=1,
        success_rate=0.875, avg_duration_ms=120.0, last_used_at="x",
    )

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve_empty),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=_make_matched_workflow()),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=stats),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "send me the weekly briefing",
            })

    assert resp.status_code == 200
    procedure_block = next(
        (m for m in captured_messages if "KNOWN PROCEDURE" in m.get("content", "")),
        None,
    )
    assert procedure_block is not None
    assert "[7/8 succeeded]" in procedure_block["content"]


@pytest.mark.asyncio
async def test_matched_workflow_no_annotation_without_stats():
    """No logged history for the tool → no success-rate suffix, just the
    plain step line (unchanged format)."""
    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "Here's the briefing."

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve_empty),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=_make_matched_workflow()),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "send me the weekly briefing",
            })

    assert resp.status_code == 200
    procedure_block = next(
        (m for m in captured_messages if "KNOWN PROCEDURE" in m.get("content", "")),
        None,
    )
    assert procedure_block is not None
    assert "succeeded]" not in procedure_block["content"]
    assert "web_search" in procedure_block["content"]

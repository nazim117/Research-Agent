# test_deep_research.py — route-level tests for POST /chat's opt-in
# deep_research loop. Same mocking style as test_chat_prompt.py: real
# FastAPI route via AsyncClient, module-level references in `main` patched
# out so no real Qdrant/Ollama/SQLite/mcp-server is required.

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

FAKE_PROJECT_ID = "proj-test-1234"
FAKE_SESSION_ID = "sess-test-5678"
FAKE_EMBED_VEC = [0.0] * 768


async def _fake_embed_cached(_cache, _project_id, _text: str) -> list[float]:
    return FAKE_EMBED_VEC


async def _fake_retrieve_empty(_project_id, _query, k, vstore, score_threshold=None, exclude_sources=None, cache=None):
    return []


def _common_patches():
    return [
        patch("main.embed_cached", side_effect=_fake_embed_cached),
        patch("main.rag.retrieve", side_effect=_fake_retrieve_empty),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set()),
        patch("main.workflow_store.find_matching", new_callable=AsyncMock, return_value=None),
        patch("main.entity_store.find_matching", new_callable=AsyncMock, return_value=[]),
        patch("main.toolbox_store.get_stats_for_tool", new_callable=AsyncMock, return_value=None),
    ]


@pytest.mark.asyncio
async def test_deep_research_end_to_end_runs_loop_and_writes_task():
    replies = [
        '<<TOOL_CALL>>{"tool": "web_search", "arguments": {"query": "x"}}<<END>>',
        "Final answer from research",
    ]

    async def _spy_chat(messages):
        return replies.pop(0)

    with ExitStack() as stack:
        for cm in _common_patches():
            stack.enter_context(cm)
        stack.enter_context(patch("main.chat", side_effect=_spy_chat))
        mock_mcp_call = stack.enter_context(
            patch("main._mcp.call", new_callable=AsyncMock, return_value={"results": ["hit"]})
        )
        mock_create = stack.enter_context(
            patch(
                "main.task_store.create",
                new_callable=AsyncMock,
                return_value=type("T", (), {"id": "task-123"})(),
            )
        )
        mock_add_step = stack.enter_context(patch("main.task_store.add_step", new_callable=AsyncMock))
        mock_mark_done = stack.enter_context(patch("main.task_store.mark_done", new_callable=AsyncMock))

        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/chat",
                json={
                    "project_id": FAKE_PROJECT_ID,
                    "session_id": FAKE_SESSION_ID,
                    "message": "research something",
                    "deep_research": True,
                },
            )

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Final answer from research"

    mock_create.assert_awaited_once_with(FAKE_PROJECT_ID, FAKE_SESSION_ID, "research something")
    mock_mcp_call.assert_awaited_once_with("web_search", {"query": "x"}, project_id=FAKE_PROJECT_ID)
    mock_add_step.assert_awaited_once_with("task-123", "web_search", {"query": "x"}, '{"results": ["hit"]}')
    mock_mark_done.assert_awaited_once_with("task-123", "Final answer from research")


@pytest.mark.asyncio
async def test_deep_research_write_tool_is_queued_not_executed():
    replies = [
        '<<TOOL_CALL>>{"tool": "memory_set", "arguments": {"key": "k", "value": "v"}}<<END>>',
        "Final answer from research",
    ]

    async def _spy_chat(messages):
        return replies.pop(0)

    with ExitStack() as stack:
        for cm in _common_patches():
            stack.enter_context(cm)
        stack.enter_context(patch("main.chat", side_effect=_spy_chat))
        mock_mcp_call = stack.enter_context(
            patch("main._mcp.call", new_callable=AsyncMock, return_value={"results": ["hit"]})
        )
        stack.enter_context(
            patch(
                "main.task_store.create",
                new_callable=AsyncMock,
                return_value=type("T", (), {"id": "task-123"})(),
            )
        )
        stack.enter_context(patch("main.task_store.add_step", new_callable=AsyncMock))
        stack.enter_context(patch("main.task_store.mark_done", new_callable=AsyncMock))
        mock_create_pending = stack.enter_context(
            patch("main.action_store.create_pending", new_callable=AsyncMock, return_value="action-123")
        )

        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/chat",
                json={
                    "project_id": FAKE_PROJECT_ID,
                    "session_id": FAKE_SESSION_ID,
                    "message": "remember something",
                    "deep_research": True,
                },
            )

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Final answer from research"

    mock_create_pending.assert_awaited_once_with(
        FAKE_PROJECT_ID, "memory_set", {"key": "k", "value": "v"}
    )
    mock_mcp_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_deep_research_false_leaves_existing_behavior_unchanged():
    async def _spy_chat(messages):
        return "plain reply"

    with ExitStack() as stack:
        for cm in _common_patches():
            stack.enter_context(cm)
        mock_chat = stack.enter_context(patch("main.chat", side_effect=_spy_chat))
        mock_create = stack.enter_context(patch("main.task_store.create", new_callable=AsyncMock))
        mock_loop = stack.enter_context(patch("main.deep_research.run_research_loop", new_callable=AsyncMock))

        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/chat",
                json={
                    "project_id": FAKE_PROJECT_ID,
                    "session_id": FAKE_SESSION_ID,
                    "message": "just a normal question",
                },
            )

    assert resp.status_code == 200
    assert resp.json()["reply"] == "plain reply"
    assert mock_chat.call_count == 1
    mock_create.assert_not_awaited()
    mock_loop.assert_not_awaited()

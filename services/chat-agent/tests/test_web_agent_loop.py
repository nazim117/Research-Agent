# tests/test_web_agent_loop.py — the agentic web tool-calling loop in /chat.
#
# openai_compatible only: when req.web_search=true and the provider supports
# tool-calling, the model gets web_search/web_fetch as real tools and can
# call them (search, fetch, search again...) in a bounded loop instead of
# getting one pre-fetched batch of results. See main.py's
# _run_web_agent_loop / _execute_web_tool and llm.chat_with_tools.
#
# The ollama/fixed-pipeline path is covered separately in
# test_chat_prompt.py (test_web_search_on_injects_results_and_citations,
# test_web_search_failure_degrades_gracefully).
#
# Approach: same seam-test style as test_chat_prompt.py — real /chat route,
# real FastAPI app, all I/O (embed, rag.retrieve, store, vstore, _mcp,
# llm.chat_with_tools) replaced with fakes/mocks.

import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from llm import LLMToolResult, ToolCall
from mcp_client import MCPError

FAKE_PROJECT_ID = "proj-test-1234"
FAKE_SESSION_ID = "sess-test-5678"
FAKE_EMBED_VEC = [0.0] * 768

FAKE_TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "", "parameters": {}}},
    {"type": "function", "function": {"name": "web_fetch", "description": "", "parameters": {}}},
]

FAKE_SEARCH_RESULTS = {
    "results": [
        {"title": "Qdrant 1.12 release notes", "url": "https://example.com/qdrant-1.12", "snippet": "New in 1.12..."},
    ]
}

FAKE_FETCH_RESULT = {
    "url": "https://example.com/qdrant-1.12",
    "status": 200,
    "body": "Full release notes text.",
    "truncated": False,
}


async def _fake_embed(_text: str) -> list[float]:
    return FAKE_EMBED_VEC


async def _fake_retrieve_empty(_project_id, _query, k, vstore, score_threshold=None, exclude_sources=None):
    return []


def _enter_base_patches(stack: contextlib.ExitStack) -> None:
    """Patches shared by every test below — I/O that isn't the point of any
    individual test (embedding, RAG, SQLite/Qdrant persistence, project
    validation) plus pinning the provider to openai_compatible so the
    agentic loop actually engages."""
    stack.enter_context(patch("main.embed", side_effect=_fake_embed))
    stack.enter_context(patch("main.rag.retrieve", side_effect=_fake_retrieve_empty))
    stack.enter_context(patch("main.store.history", new_callable=AsyncMock, return_value=[]))
    stack.enter_context(patch("main.store.append", new_callable=AsyncMock))
    stack.enter_context(patch("main.vstore.search", new_callable=AsyncMock, return_value=[]))
    stack.enter_context(patch("main.vstore.upsert", new_callable=AsyncMock))
    stack.enter_context(patch("main._require_project", new_callable=AsyncMock))
    stack.enter_context(
        patch("main.document_state_store.get_disabled_sources", new_callable=AsyncMock, return_value=set())
    )
    stack.enter_context(patch("main.settings.llm_provider", "openai_compatible"))


async def _post_chat(web_search: bool = True):
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/chat", json={
            "project_id": FAKE_PROJECT_ID,
            "session_id": FAKE_SESSION_ID,
            "message": "What's new in the latest Qdrant release?",
            "web_search": web_search,
        })


@pytest.mark.asyncio
async def test_agentic_loop_one_search_round_then_answer():
    """chat_with_tools returns one web_search tool call, then a final
    content-only response — _mcp.call runs the search, and the reply's
    citations reflect the search result."""
    call_count = 0

    async def _fake_chat_with_tools(messages, tools):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMToolResult(
                content=None,
                tool_calls=[ToolCall(id="call_1", name="web_search", arguments={"query": "qdrant release", "limit": 5})],
            )
        return LLMToolResult(content="Qdrant 1.12 added X [1].", tool_calls=[])

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.chat_with_tools", side_effect=_fake_chat_with_tools))
        stack.enter_context(patch("main._get_web_tool_schemas", new_callable=AsyncMock, return_value=FAKE_TOOLS))
        mcp = stack.enter_context(patch("main._mcp"))
        mcp.call = AsyncMock(return_value=FAKE_SEARCH_RESULTS)
        resp = await _post_chat()

    assert resp.status_code == 200
    mcp.call.assert_awaited_once_with("web_search", {"query": "qdrant release", "limit": 5})
    assert call_count == 2

    data = resp.json()
    assert data["reply"] == "Qdrant 1.12 added X [1]."
    assert data["citations"] == [
        {"ref": 1, "source": "https://example.com/qdrant-1.12", "chunk_index": 0}
    ]


@pytest.mark.asyncio
async def test_agentic_loop_search_then_fetch_dedupes_citation():
    """Model searches, then fetches the same URL it found — the URL gets one
    citation ref, reused across both tool results."""
    call_count = 0

    async def _fake_chat_with_tools(messages, tools):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMToolResult(
                content=None,
                tool_calls=[ToolCall(id="call_1", name="web_search", arguments={"query": "qdrant release", "limit": 5})],
            )
        if call_count == 2:
            return LLMToolResult(
                content=None,
                tool_calls=[ToolCall(id="call_2", name="web_fetch", arguments={"url": "https://example.com/qdrant-1.12"})],
            )
        return LLMToolResult(content="Qdrant 1.12 added X [1].", tool_calls=[])

    async def _mcp_call(name, arguments):
        return FAKE_SEARCH_RESULTS if name == "web_search" else FAKE_FETCH_RESULT

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.chat_with_tools", side_effect=_fake_chat_with_tools))
        stack.enter_context(patch("main._get_web_tool_schemas", new_callable=AsyncMock, return_value=FAKE_TOOLS))
        mcp = stack.enter_context(patch("main._mcp"))
        mcp.call = AsyncMock(side_effect=_mcp_call)
        resp = await _post_chat()

    assert resp.status_code == 200
    assert mcp.call.await_count == 2
    assert call_count == 3

    data = resp.json()
    assert data["citations"] == [
        {"ref": 1, "source": "https://example.com/qdrant-1.12", "chunk_index": 0}
    ]


@pytest.mark.asyncio
async def test_agentic_loop_round_cap_forces_final_answer():
    """If the model never stops calling tools, the loop stops at
    chat_tool_max_rounds and makes one final chat_with_tools call with
    tool_choice="none" to force a text answer instead of looping forever.

    Uses chat_with_tools (not plain chat()) for that final call too — plain
    chat() with messages still full of tool-calling turns is exactly what
    triggers deepseek-chat's DSML markup leak (see test_llm.py)."""
    call_count = 0

    async def _fake_chat_with_tools(messages, tools, tool_choice="auto"):
        nonlocal call_count
        call_count += 1
        if tool_choice == "none":
            return LLMToolResult(content="Giving up after the cap — here's my best answer.", tool_calls=[])
        return LLMToolResult(
            content=None,
            tool_calls=[ToolCall(id="call_x", name="web_search", arguments={"query": "x", "limit": 5})],
        )

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.settings.chat_tool_max_rounds", 2))
        stack.enter_context(patch("main.chat_with_tools", side_effect=_fake_chat_with_tools))
        stack.enter_context(patch("main._get_web_tool_schemas", new_callable=AsyncMock, return_value=FAKE_TOOLS))
        mcp = stack.enter_context(patch("main._mcp"))
        mcp.call = AsyncMock(return_value=FAKE_SEARCH_RESULTS)
        resp = await _post_chat()

    assert resp.status_code == 200
    assert call_count == 3  # 2 in-loop rounds (capped) + 1 forced final call
    assert resp.json()["reply"] == "Giving up after the cap — here's my best answer."


@pytest.mark.asyncio
async def test_agentic_loop_round_cap_ignores_tool_calls_on_forced_final_call():
    """Even if the forced tool_choice="none" call still returns tool_calls
    (deepseek-chat's known quirk means it doesn't always respect
    tool_choice="none"), the round budget is spent — they must be ignored,
    not executed, and a sensible fallback message used since there's no
    clean content either."""
    async def _always_tool_call(messages, tools, tool_choice="auto"):
        return LLMToolResult(
            content=None,
            tool_calls=[ToolCall(id="call_x", name="web_search", arguments={"query": "x", "limit": 5})],
        )

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.settings.chat_tool_max_rounds", 1))
        stack.enter_context(patch("main.chat_with_tools", side_effect=_always_tool_call))
        stack.enter_context(patch("main._get_web_tool_schemas", new_callable=AsyncMock, return_value=FAKE_TOOLS))
        mcp = stack.enter_context(patch("main._mcp"))
        mcp.call = AsyncMock(return_value=FAKE_SEARCH_RESULTS)
        resp = await _post_chat()

    assert resp.status_code == 200
    # mcp.call only for the one real in-loop round — the forced final call's
    # tool_calls must never reach _mcp.call.
    assert mcp.call.await_count == 1
    assert "couldn't finish" in resp.json()["reply"]


@pytest.mark.asyncio
async def test_agentic_loop_round_cap_ignores_narration_alongside_recovered_tool_calls():
    """The exact bug reproduced live: on the forced tool_choice="none" call,
    deepseek-chat still wants to call a tool and expresses that as DSML
    markup alongside genuine-looking prose ("Let me fetch the raw markdown
    version..."). chat_with_tools recovers the tool call and strips the
    markup, leaving that prose as `content` — but it's just the narration
    lead-in to a call we're refusing to execute, never a real answer. Must
    use the fallback message, not that leftover prose."""
    async def _fake_chat_with_tools(messages, tools, tool_choice="auto"):
        if tool_choice == "none":
            return LLMToolResult(
                content="Let me fetch the raw markdown version instead.",
                tool_calls=[ToolCall(id="leaked-0-0", name="web_fetch", arguments={"url": "https://x/index.md"})],
            )
        return LLMToolResult(
            content=None,
            tool_calls=[ToolCall(id="call_x", name="web_search", arguments={"query": "x", "limit": 5})],
        )

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.settings.chat_tool_max_rounds", 1))
        stack.enter_context(patch("main.chat_with_tools", side_effect=_fake_chat_with_tools))
        stack.enter_context(patch("main._get_web_tool_schemas", new_callable=AsyncMock, return_value=FAKE_TOOLS))
        mcp = stack.enter_context(patch("main._mcp"))
        mcp.call = AsyncMock(return_value=FAKE_SEARCH_RESULTS)
        resp = await _post_chat()

    assert resp.status_code == 200
    reply = resp.json()["reply"]
    assert "Let me fetch the raw markdown" not in reply
    assert "couldn't finish" in reply
    # The forced final call's recovered tool call must never be executed —
    # only the one real in-loop round's call should have reached _mcp.call.
    assert mcp.call.await_count == 1


# ---------------------------------------------------------------------------
# Truncated-mid-thought rounds (finish_reason="length") — confirmed live
# against DeepSeek: a response can come back with empty tool_calls and
# narration-only content ("let me try X instead...") purely because
# max_tokens cut it off before it reached the tool-call JSON, not because
# the model was actually done. Must be retried, not treated as final.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agentic_loop_retries_truncated_round_instead_of_ending():
    """A finish_reason="length" round with no tool_calls must not end the
    loop — it should nudge for brevity and retry, then use the next round's
    (real) tool call / answer."""
    call_count = 0

    async def _fake_chat_with_tools(messages, tools, tool_choice="auto"):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMToolResult(
                content="The GitHub page requires JavaScript. Let me try the API instead,",
                tool_calls=[],
                finish_reason="length",
            )
        return LLMToolResult(content="Qdrant 1.12 added X [1].", tool_calls=[], finish_reason="stop")

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.chat_with_tools", side_effect=_fake_chat_with_tools))
        stack.enter_context(patch("main._get_web_tool_schemas", new_callable=AsyncMock, return_value=FAKE_TOOLS))
        stack.enter_context(patch("main._mcp"))
        resp = await _post_chat()

    assert resp.status_code == 200
    assert call_count == 2
    assert resp.json()["reply"] == "Qdrant 1.12 added X [1]."


@pytest.mark.asyncio
async def test_agentic_loop_truncated_round_nudges_for_brevity():
    """The retry after a truncated round must include a brevity nudge in
    the conversation sent back to the model, and must NOT keep the
    truncated (incomplete, not useful) assistant turn in history."""
    captured_messages_by_call: list[list[dict]] = []

    async def _fake_chat_with_tools(messages, tools, tool_choice="auto"):
        captured_messages_by_call.append([dict(m) for m in messages])
        if len(captured_messages_by_call) == 1:
            return LLMToolResult(content="cut off mid-th", tool_calls=[], finish_reason="length")
        return LLMToolResult(content="final answer", tool_calls=[], finish_reason="stop")

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.chat_with_tools", side_effect=_fake_chat_with_tools))
        stack.enter_context(patch("main._get_web_tool_schemas", new_callable=AsyncMock, return_value=FAKE_TOOLS))
        stack.enter_context(patch("main._mcp"))
        resp = await _post_chat()

    assert resp.status_code == 200
    assert len(captured_messages_by_call) == 2
    first_call_messages = captured_messages_by_call[0]
    second_call_messages = captured_messages_by_call[1]
    # The retry's message list must have exactly one more message than the
    # first call's (the nudge) — no truncated assistant turn was appended.
    assert len(second_call_messages) == len(first_call_messages) + 1
    nudge = second_call_messages[-1]
    assert nudge["role"] == "system"
    assert "cut off" in nudge["content"]


@pytest.mark.asyncio
async def test_agentic_loop_persistent_truncation_falls_through_to_round_cap():
    """If every round truncates, the loop still terminates at
    chat_tool_max_rounds (retries consume the same round budget, not an
    unbounded separate one) via the existing round-cap fallback."""
    call_count = 0

    async def _always_truncated(messages, tools, tool_choice="auto"):
        nonlocal call_count
        call_count += 1
        if tool_choice == "none":
            return LLMToolResult(content="forced final answer", tool_calls=[], finish_reason="stop")
        return LLMToolResult(content="still thinking...", tool_calls=[], finish_reason="length")

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.settings.chat_tool_max_rounds", 2))
        stack.enter_context(patch("main.chat_with_tools", side_effect=_always_truncated))
        stack.enter_context(patch("main._get_web_tool_schemas", new_callable=AsyncMock, return_value=FAKE_TOOLS))
        stack.enter_context(patch("main._mcp"))
        resp = await _post_chat()

    assert resp.status_code == 200
    assert call_count == 3  # 2 in-loop rounds (both truncated, retried) + 1 forced final call
    assert resp.json()["reply"] == "forced final answer"


@pytest.mark.asyncio
async def test_agentic_loop_schema_fetch_failure_degrades_gracefully():
    """If fetching tool schemas from mcp-server fails, the turn still
    succeeds — the model is told search failed and chat_with_tools is never
    invoked (no tools loop attempted)."""
    captured_messages = []

    async def _fake_chat(messages):
        captured_messages.extend(messages)
        return "I couldn't search, but here's what I know."

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.chat", side_effect=_fake_chat))
        mock_cwt = stack.enter_context(patch("main.chat_with_tools", new_callable=AsyncMock))
        stack.enter_context(
            patch("main._get_web_tool_schemas", new_callable=AsyncMock, side_effect=MCPError("mcp-server unreachable"))
        )
        stack.enter_context(patch("main._mcp"))
        resp = await _post_chat()

    assert resp.status_code == 200
    mock_cwt.assert_not_called()
    assert resp.json()["citations"] == []
    error_block = next((m for m in captured_messages if "search failed" in m.get("content", "")), None)
    assert error_block is not None
    assert "mcp-server unreachable" in error_block["content"]


@pytest.mark.asyncio
async def test_web_search_off_never_fetches_tool_schemas():
    """req.web_search=False must never fetch tool schemas or call
    chat_with_tools, even on the openai_compatible provider."""
    async def _fake_chat(messages):
        return "answer"

    with contextlib.ExitStack() as stack:
        _enter_base_patches(stack)
        stack.enter_context(patch("main.chat", side_effect=_fake_chat))
        mock_cwt = stack.enter_context(patch("main.chat_with_tools", new_callable=AsyncMock))
        mock_schemas = stack.enter_context(patch("main._get_web_tool_schemas", new_callable=AsyncMock))
        stack.enter_context(patch("main._mcp"))
        resp = await _post_chat(web_search=False)

    assert resp.status_code == 200
    mock_schemas.assert_not_called()
    mock_cwt.assert_not_called()

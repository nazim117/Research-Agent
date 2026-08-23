"""Unit tests for llm.py — deploy-time-only LLM dispatcher.

Provider and model are fixed by env vars (LLM_PROVIDER, OPENAI_*, OLLAMA_*).
End-user requests cannot override them.  All HTTP calls are intercepted with
unittest.mock — no real network needed.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import llm as llm_module
from llm import chat, chat_with_tools, validate_llm_config
from llm import _parse_leaked_tool_calls

MESSAGES = [{"role": "user", "content": "hello"}]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mock_response(status: int, body: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.text = json.dumps(body)
    return r


def _ollama_ok(content: str) -> MagicMock:
    return _mock_response(200, {"message": {"role": "assistant", "content": content}})


def _openai_ok(content: str) -> MagicMock:
    return _mock_response(
        200, {"choices": [{"message": {"role": "assistant", "content": content}}]}
    )


# ─── Ollama backend ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ollama_returns_content():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=_ollama_ok("hi from ollama"))
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.llm_provider = "ollama"
            s.ollama_base_url = "http://localhost:11434"
            s.ollama_chat_model = "llama3"
            result = await chat(MESSAGES)
    assert result == "hi from ollama"


@pytest.mark.asyncio
async def test_ollama_502_on_non_200():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(
        return_value=_mock_response(500, {"error": "internal"})
    )
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.ollama_base_url = "http://localhost:11434"
            s.ollama_chat_model = "llama3"
            with pytest.raises(HTTPException) as exc:
                await llm_module._chat_ollama(MESSAGES)
    assert exc.value.status_code == 502


# ─── OpenAI-compatible backend ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openai_compatible_returns_content():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(
        return_value=_openai_ok("hi from openai-compatible")
    )
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.openai_base_url = "https://models.github.ai/inference"
            s.openai_api_key = "ghp_test"
            s.openai_model = "openai/gpt-4o-mini"
            s.openai_provider_label = "GitHub Models"
            result = await llm_module._chat_openai_compatible(MESSAGES)
    # _chat_openai_compatible returns (reply, usage) tuple.
    assert result[0] == "hi from openai-compatible"


@pytest.mark.asyncio
async def test_openai_compatible_502_on_non_200():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(
        return_value=_mock_response(401, {"error": "unauthorized"})
    )
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.openai_base_url = "https://models.github.ai/inference"
            s.openai_api_key = "bad-key"
            s.openai_model = "openai/gpt-4o-mini"
            s.openai_provider_label = "GitHub Models"
            with pytest.raises(HTTPException) as exc:
                await llm_module._chat_openai_compatible(MESSAGES)
    assert exc.value.status_code == 502


# ─── chat_with_tools (openai_compatible only) ─────────────────────────────────

FAKE_TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "", "parameters": {}}}
]


@pytest.mark.asyncio
async def test_chat_with_tools_requires_openai_compatible():
    with patch("llm.settings") as s:
        s.llm_provider = "ollama"
        with pytest.raises(HTTPException) as exc:
            await chat_with_tools(MESSAGES, FAKE_TOOLS)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_chat_with_tools_parses_tool_calls():
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": json.dumps({"query": "qdrant release notes", "limit": 5}),
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=_mock_response(200, body))
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.llm_provider = "openai_compatible"
            s.openai_base_url = "https://api.example.com"
            s.openai_api_key = "test-key"
            s.openai_model = "gpt-test"
            s.openai_provider_label = "Example"
            result = await chat_with_tools(MESSAGES, FAKE_TOOLS)

    assert result.content is None
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "web_search"
    assert tc.arguments == {"query": "qdrant release notes", "limit": 5}
    assert result.usage["total_tokens"] == 15


@pytest.mark.asyncio
async def test_chat_with_tools_plain_content_no_tool_calls():
    body = {"choices": [{"message": {"role": "assistant", "content": "no tools needed"}}]}
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=_mock_response(200, body))
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.llm_provider = "openai_compatible"
            s.openai_base_url = "https://api.example.com"
            s.openai_api_key = "test-key"
            s.openai_model = "gpt-test"
            s.openai_provider_label = "Example"
            result = await chat_with_tools(MESSAGES, FAKE_TOOLS)

    assert result.content == "no tools needed"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_chat_with_tools_exposes_finish_reason():
    """Callers need finish_reason to tell a truncated response (cut off
    mid-thought, finish_reason="length") apart from a genuine final answer
    (finish_reason="stop") when tool_calls comes back empty either way —
    see main.py's _run_web_agent_loop."""
    body = {
        "choices": [{
            "message": {"role": "assistant", "content": "let me try a different approach..."},
            "finish_reason": "length",
        }],
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=_mock_response(200, body))
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.llm_provider = "openai_compatible"
            s.openai_base_url = "https://api.example.com"
            s.openai_api_key = "test-key"
            s.openai_model = "gpt-test"
            s.openai_provider_label = "Example"
            result = await chat_with_tools(MESSAGES, FAKE_TOOLS)

    assert result.finish_reason == "length"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_chat_with_tools_sends_generous_max_tokens():
    """1024 (plain chat()'s budget) was confirmed live to truncate
    mid-thought in a growing tool-calling conversation — chat_with_tools
    needs more headroom than a single-shot answer."""
    body = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
    mock_client = AsyncMock()
    mock_post = AsyncMock(return_value=_mock_response(200, body))
    mock_client.__aenter__.return_value.post = mock_post
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.llm_provider = "openai_compatible"
            s.openai_base_url = "https://api.example.com"
            s.openai_api_key = "test-key"
            s.openai_model = "gpt-test"
            s.openai_provider_label = "Example"
            await chat_with_tools(MESSAGES, FAKE_TOOLS)

    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["max_tokens"] >= 4096


# ─── DeepSeek DSML leaked-tool-call recovery ───────────────────────────────────
#
# deepseek-chat sometimes emits its internal tool-call markup literally in
# `content` instead of populating the API's structured `tool_calls` field —
# reproduced live against DeepSeek's API (both tool_choice omitted and
# tool_choice="none"). LEAKED_DSML_CONTENT below is the exact text captured
# from that reproduction.

LEAKED_DSML_CONTENT = (
    "The fetch didn't get the content. Let me try the GitHub API for release "
    "details.\n\n"
    '<｜｜DSML｜｜tool_calls>\n'
    '<｜｜DSML｜｜invoke name="web_fetch">\n'
    '<｜｜DSML｜｜parameter name="url" string="true">'
    "https://api.github.com/repos/qdrant/qdrant/releases/latest"
    "</｜｜DSML｜｜parameter>\n"
    "</｜｜DSML｜｜invoke>\n"
    "</｜｜DSML｜｜tool_calls>"
)


def test_parse_leaked_tool_calls_recovers_call_and_strips_markup():
    cleaned, calls = _parse_leaked_tool_calls(LEAKED_DSML_CONTENT)

    assert cleaned == "The fetch didn't get the content. Let me try the GitHub API for release details."
    assert len(calls) == 1
    assert calls[0].name == "web_fetch"
    assert calls[0].arguments == {"url": "https://api.github.com/repos/qdrant/qdrant/releases/latest"}


def test_parse_leaked_tool_calls_coerces_non_string_params():
    content = (
        '<｜｜DSML｜｜tool_calls>\n'
        '<｜｜DSML｜｜invoke name="web_search">\n'
        '<｜｜DSML｜｜parameter name="query" string="true">qdrant changelog</｜｜DSML｜｜parameter>\n'
        '<｜｜DSML｜｜parameter name="limit" string="false">3</｜｜DSML｜｜parameter>\n'
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>"
    )
    _cleaned, calls = _parse_leaked_tool_calls(content)
    assert calls[0].arguments == {"query": "qdrant changelog", "limit": 3}


def test_parse_leaked_tool_calls_returns_empty_for_normal_content():
    cleaned, calls = _parse_leaked_tool_calls("just a normal answer, no markup here")
    assert cleaned == "just a normal answer, no markup here"
    assert calls == []


@pytest.mark.asyncio
async def test_chat_with_tools_recovers_leaked_dsml_when_tool_calls_empty():
    """The exact failure mode reported: DeepSeek returns tool_calls=null but
    stuffs its tool-call intent into content as DSML markup. chat_with_tools
    must recover it as a real tool call, not hand the raw markup to the
    caller as if it were the final answer."""
    body = {
        "choices": [{"message": {"role": "assistant", "content": LEAKED_DSML_CONTENT, "tool_calls": None}}],
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=_mock_response(200, body))
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.llm_provider = "openai_compatible"
            s.openai_base_url = "https://api.deepseek.com/v1"
            s.openai_api_key = "test-key"
            s.openai_model = "deepseek-chat"
            s.openai_provider_label = "DeepSeek"
            result = await chat_with_tools(MESSAGES, FAKE_TOOLS)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "web_fetch"
    assert "DSML" not in (result.content or "")
    assert "｜" not in (result.content or "")


@pytest.mark.asyncio
async def test_chat_with_tools_does_not_run_dsml_recovery_when_real_tool_calls_present():
    """Real structured tool_calls (the normal, working case) must not be
    touched by the DSML-recovery path even if content happens to be present
    alongside them."""
    body = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "checking now",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "x"}'}}
                ],
            }
        }],
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=_mock_response(200, body))
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.llm_provider = "openai_compatible"
            s.openai_base_url = "https://api.example.com"
            s.openai_api_key = "test-key"
            s.openai_model = "gpt-test"
            s.openai_provider_label = "Example"
            result = await chat_with_tools(MESSAGES, FAKE_TOOLS)

    assert result.content == "checking now"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "web_search"


# ─── Dispatcher ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatcher_routes_ollama():
    # _chat_ollama returns (reply, usage) — mock must match.
    with patch("llm._chat_ollama", new_callable=AsyncMock, return_value=("ollama-reply", {})) as mock_fn:
        with patch("llm.settings") as s:
            s.llm_provider = "ollama"
            result = await chat(MESSAGES)
    mock_fn.assert_called_once_with(MESSAGES)
    assert result == "ollama-reply"


@pytest.mark.asyncio
async def test_dispatcher_routes_openai_compatible():
    # _chat_openai_compatible returns (reply, usage) — mock must match.
    with patch("llm._chat_openai_compatible", new_callable=AsyncMock, return_value=("cloud-reply", {})) as mock_fn:
        with patch("llm.settings") as s:
            s.llm_provider = "openai_compatible"
            result = await chat(MESSAGES)
    mock_fn.assert_called_once_with(MESSAGES)
    assert result == "cloud-reply"


@pytest.mark.asyncio
async def test_dispatcher_unknown_provider_raises_500():
    with patch("llm.settings") as s:
        s.llm_provider = "unknown-provider"
        with pytest.raises(HTTPException) as exc:
            await chat(MESSAGES)
    assert exc.value.status_code == 500
    assert "unknown-provider" in exc.value.detail


# ─── Startup validation ────────────────────────────────────────────────────────

def test_validate_ollama_ok():
    with patch("llm.settings") as s:
        s.llm_provider = "ollama"
        s.ollama_chat_model = "llama3"
        validate_llm_config()  # must not raise


def test_validate_ollama_empty_model_raises():
    with patch("llm.settings") as s:
        s.llm_provider = "ollama"
        s.ollama_chat_model = ""
        with pytest.raises(RuntimeError, match="OLLAMA_CHAT_MODEL"):
            validate_llm_config()


def test_validate_openai_compatible_ok():
    with patch("llm.settings") as s:
        s.llm_provider = "openai_compatible"
        s.openai_base_url = "https://models.github.ai/inference"
        s.openai_api_key = "ghp_test"
        s.openai_model = "openai/gpt-4o-mini"
        validate_llm_config()  # must not raise


@pytest.mark.parametrize("missing_field", [
    "openai_base_url",
    "openai_api_key",
    "openai_model",
])
def test_validate_openai_missing_field_raises(missing_field):
    with patch("llm.settings") as s:
        s.llm_provider = "openai_compatible"
        s.openai_base_url = "https://models.github.ai/inference"
        s.openai_api_key = "ghp_test"
        s.openai_model = "openai/gpt-4o-mini"
        setattr(s, missing_field, "")
        with pytest.raises(RuntimeError, match=missing_field.upper()):
            validate_llm_config()


def test_validate_unknown_provider_raises():
    with patch("llm.settings") as s:
        s.llm_provider = "bad-provider"
        with pytest.raises(RuntimeError, match="bad-provider"):
            validate_llm_config()

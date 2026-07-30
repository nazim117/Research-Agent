# tests/test_llm_integration.py — real Ollama LLM chat integration tests.
#
# These tests call llm.chat() against a live Ollama chat model.
# Skipped when Ollama is unreachable (conftest.ollama_up).
#
# The tests use LLM_PROVIDER=ollama (the default).  They assert on response
# *shape* only (non-empty string), not exact wording — LLM output is
# non-deterministic and different models have different verbosity.
#
# What is covered (the "llm.py gap" from docs/test-suite-analysis.md §5.2):
#   - chat() returns a non-empty string for a basic user message
#   - the 502 error path when Ollama is unreachable is NOT tested here
#     (that is already covered by test_llm.py via mock)

import pytest

pytestmark = pytest.mark.integration


async def test_chat_ollama_returns_string(ollama_chat_model_up, monkeypatch):
    """chat() (provider=ollama) returns a non-empty string reply."""
    import llm
    from config import settings

    # Ensure we test the Ollama path regardless of the local .env setting.
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    messages = [{"role": "user", "content": "Reply with exactly the word: pong"}]
    reply = await llm.chat(messages)

    assert isinstance(reply, str), "chat() must return a string"
    assert len(reply.strip()) > 0, "chat() must not return an empty string"


async def test_chat_multi_turn(ollama_chat_model_up, monkeypatch):
    """chat() handles a two-turn conversation without raising."""
    import llm
    from config import settings

    monkeypatch.setattr(settings, "llm_provider", "ollama")

    messages = [
        {"role": "user", "content": "What is 2 + 2?"},
        {"role": "assistant", "content": "4"},
        {"role": "user", "content": "What is that result plus 1?"},
    ]
    reply = await llm.chat(messages)

    assert isinstance(reply, str)
    assert len(reply.strip()) > 0

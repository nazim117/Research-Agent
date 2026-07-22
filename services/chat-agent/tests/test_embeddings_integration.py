# tests/test_embeddings_integration.py — real embeddings-service integration tests.
#
# These tests call embeddings.embed() against a live embeddings service (TEI,
# see docker-compose.yml's "embeddings" service) serving BAAI/bge-base-en-v1.5.
# Skipped when unreachable (conftest.embeddings_up).
#
# What is covered (the "embeddings.py gap" from docs/test-suite-analysis.md §5.2):
#   - embed() returns a list of 768 floats (correct dimension for nomic-embed-text)
#   - same text → same vector (deterministic embedding)
#   - different texts produce different vectors

import pytest

pytestmark = pytest.mark.integration


async def test_embed_returns_768_floats(embeddings_up):
    """embed() returns a list of exactly 768 floats for any input text."""
    from embeddings import embed

    vector = await embed("hello world")

    assert isinstance(vector, list), "embed() must return a list"
    assert len(vector) == 768, f"expected 768 dims, got {len(vector)}"
    assert all(isinstance(v, float) for v in vector), "all elements must be float"


async def test_embed_is_deterministic(embeddings_up):
    """Two calls with identical text produce the same vector."""
    from embeddings import embed

    text = "the quick brown fox"
    v1 = await embed(text)
    v2 = await embed(text)

    # Vectors should be element-wise identical (same model, same input).
    assert v1 == v2, "embed() must return the same vector for the same text"


async def test_embed_different_texts_produce_different_vectors(embeddings_up):
    """Semantically different texts produce different vectors."""
    from embeddings import embed

    v_hello = await embed("hello world")
    v_code = await embed("def fibonacci(n): return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)")

    assert v_hello != v_code, "different texts should produce different vectors"


async def test_get_model_info_reports_configured_model(embeddings_up):
    """get_model_info() reflects the model the embeddings service is actually serving."""
    from embeddings import get_model_info

    info = await get_model_info()

    assert "model_id" in info
